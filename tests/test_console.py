"""Tests for the console's job runner: what happens when a page fails, when a
job spans several sheets, and when a job would run off the work area.

These are the paths that cost paper and ink to discover by hand.

    .venv\\Scripts\\python -m tests.test_console
"""

from __future__ import annotations

import queue
import threading
import time

from ._harness import RecordingPort, check, report, run

from plotter import server as srv
from plotter.config import MachineConfig, PageConfig, TextStyle
from plotter.jobs import JobInput, bounds_warnings, build_pages
from plotter.layout import PlacedText
from plotter.stream import GrblError, GrblStreamer


def _drain(q: queue.Queue) -> list[dict]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _page(n_strokes: int = 1) -> PlacedText:
    return PlacedText(strokes=[[(0.0, 0.0), (5.0, 5.0)]] * n_strokes, page_index=0)


def test_failed_page_lifts_the_pen():
    """A GRBL error mid-page used to return with the pen still on the paper,
    bleeding a blot, while the console went on claiming to be plotting."""
    print("a page that fails mid-stream")

    class DiesPartWay(RecordingPort):
        def readline(self) -> bytes:
            # fail once the pen is down and the drawing has started
            if len(self.written) > 6:
                return b"error:9"
            return b"ok"

    port = DiesPartWay()
    session = srv.Session()
    session.streamer = GrblStreamer(port="FAKE", transport=port)
    machine = MachineConfig()
    q: queue.Queue = queue.Queue()

    srv._run_job_blocking(session, machine, [_page(6)], PageConfig(), q)
    msgs = _drain(q)
    kinds = [m["type"] for m in msgs]

    check("the failure reaches the operator", "error" in kinds, str(kinds))
    check("the job is reported as over", "jobComplete" in kinds, str(kinds))
    check("and reported as not finished",
          any(m["type"] == "jobComplete" and m["cancelled"] for m in msgs), str(msgs[-1]))
    # pen_up_cmd_value on the real machine is M03 S50
    check("the pen is lifted off the paper", "M03 S50" in port.written, str(port.written[-4:]))


def test_multi_page_waits_for_a_fresh_sheet():
    """Every page is drawn from the same zero, so without a stop between them
    the machine draws page 2 on top of page 1."""
    print("multi-page job stops between sheets")

    session = srv.Session()
    session.streamer = GrblStreamer(port="FAKE", transport=RecordingPort())
    q: queue.Queue = queue.Queue()
    pages = [_page(), _page()]

    t = threading.Thread(
        target=srv._run_job_blocking,
        args=(session, MachineConfig(), pages, PageConfig(), q),
        daemon=True,
    )
    t.start()
    time.sleep(0.5)

    kinds = [m["type"] for m in _drain(q)]
    check("it stops and asks for a new sheet", "pageWait" in kinds, str(kinds))
    check("it does not run on to the next page by itself",
          "jobComplete" not in kinds, str(kinds))
    check("the job thread is still alive, waiting", t.is_alive())

    session.page_ready.set()  # operator confirms the new sheet
    t.join(timeout=3)
    check("confirming the sheet finishes the job", not t.is_alive())
    check("and the job completes normally",
          any(m["type"] == "jobComplete" and not m["cancelled"] for m in _drain(q)))


def test_bounds_are_checked_before_a_real_run():
    """Only the simulator used to check this, so a real Run could drive the
    carriage into its end stops - there are no limit switches to stop it."""
    print("a job that runs off the work area")
    page = PageConfig(width_mm=60, height_mm=60)
    pages = build_pages(JobInput(text="hello there " * 8), page, TextStyle(), None, False, 1)

    off = bounds_warnings(pages, page, MachineConfig(origin_x_mm=400), 195, 300)
    check("an off-bed job is caught", bool(off), str(off))
    check("the warning says which axis and how far",
          "X runs" in off[0] and "195" in off[0], off[0])
    check("one line per page, not one per move", len(off) == len(pages), str(len(off)))

    fits = bounds_warnings(pages, page, MachineConfig(), 195, 300)
    check("a job that fits is not flagged", fits == [], str(fits))

    check("no measured bed means nothing to check",
          bounds_warnings(pages, page, MachineConfig(origin_x_mm=400), None, None) == [])


def test_uploaded_paths_are_confined_to_the_uploads_folder():
    """The browser sends back a server-side path, so an arbitrary one would
    have the console read and plot any file the server account can open."""
    print("uploaded file paths")
    from fastapi import HTTPException

    try:
        srv._safe_upload_path(r"C:\Windows\win.ini")
        check("a path outside jobs/uploads is refused", False, "no error raised")
    except HTTPException as e:
        check("a path outside jobs/uploads is refused", e.status_code == 400, str(e.detail))

    check("nothing supplied stays None", srv._safe_upload_path(None) is None)


def _run_notebook(order: str) -> list[dict]:
    """Run a small two-pen notebook job, answering every prompt immediately,
    and return the messages the console would have received."""
    from plotter.notebook import NotebookConfig, PenPass, build_notebook_pages

    # pen A writes on pages 1 and 3, pen B on all three
    nb = NotebookConfig(lines_per_page=2)
    passes = [
        PenPass("A", "#000", ["a1", "", "", "", "a3", ""]),
        PenPass("B", "#00f", ["", "b1", "b2", "", "", "b3"]),
    ]
    pages, _ = build_notebook_pages(passes, nb, TextStyle(font_size_mm=3), None, auto_fit=False)

    session = srv.Session()
    session.streamer = GrblStreamer(port="FAKE", transport=RecordingPort())
    q: queue.Queue = queue.Queue()

    worker = threading.Thread(
        target=srv._run_notebook_blocking,
        args=(session, MachineConfig(), pages, nb, q, order),
        daemon=True,
    )
    worker.start()

    seen: list[dict] = []
    deadline = time.time() + 20
    while worker.is_alive() and time.time() < deadline:
        try:
            msg = q.get(timeout=0.2)
        except queue.Empty:
            continue
        seen.append(msg)
        if msg["type"] in ("penChange", "pageWait"):
            session.page_ready.set()      # the operator does as they are asked
    worker.join(timeout=5)
    while not q.empty():
        seen.append(q.get_nowait())
    return seen


def test_page_order_finishes_each_page_before_turning():
    print("order: page by page")
    seen = _run_notebook("page")
    pens = [m["pen"] for m in seen if m["type"] == "penChange"]
    turns = [m for m in seen if m["type"] == "pageWait"]

    check("the notebook only moves forward one page at a time",
          all(m.get("turns", 1) == 1 for m in turns), str([m.get("turns") for m in turns]))
    check("it turns the page twice for three pages", len(turns) == 2, str(len(turns)))
    check("and swaps pen whenever the next pen differs", pens == ["A", "B", "A", "B"], str(pens))
    check("nobody is ever asked to go back to the start",
          not any(m.get("returnToStart") for m in seen if m["type"] == "penChange"))
    check("the job finishes", any(m["type"] == "jobComplete" and not m["cancelled"] for m in seen))


def test_pen_order_takes_one_pen_through_the_whole_notebook():
    """Two pen changes instead of one per page - on a 105-page record that is
    the difference between 2 swaps and 210."""
    print("order: one pen at a time")
    seen = _run_notebook("pen")
    changes = [m for m in seen if m["type"] == "penChange"]
    turns = [m for m in seen if m["type"] == "pageWait"]

    check("each pen is fitted exactly once", [m["pen"] for m in changes] == ["A", "B"],
          str([m["pen"] for m in changes]))
    check("the second pen starts with a trip back to the first page",
          changes[1]["returnToStart"] is True and changes[1]["page"] == 1, str(changes[1]))
    check("pages this pen has nothing on are skipped in one go",
          any(m.get("turns") == 2 for m in turns), str([m.get("turns") for m in turns]))
    check("and the operator is told which page to land on",
          all(m.get("nextPage") for m in turns), str(turns))
    check("the job finishes", any(m["type"] == "jobComplete" and not m["cancelled"] for m in seen))

    written = [(m["pen"], m["page"]) for m in seen if m["type"] == "passComplete"]
    check("every pen writes every page it has content for",
          written == [("A", 1), ("A", 3), ("B", 1), ("B", 2), ("B", 3)], str(written))


def test_the_cost_of_each_order_is_reported():
    """The console shows this before the job starts, so the choice is informed."""
    print("what each order costs the operator")
    from plotter.notebook import NotebookConfig, PenPass, build_notebook_pages

    pages, _ = build_notebook_pages(
        [PenPass("A", "#000", ["a1", "", "", "", "a3", ""]),
         PenPass("B", "#00f", ["", "b1", "b2", "", "", "b3"])],
        NotebookConfig(lines_per_page=2), TextStyle(font_size_mm=3), None, auto_fit=False)

    effort = srv.notebook_effort(pages)
    check("page order counts a change per pen per page",
          effort["pageOrder"]["penChanges"] == 4, str(effort["pageOrder"]))
    check("pen order counts one change per pen",
          effort["penOrder"]["penChanges"] == 2, str(effort["penOrder"]))
    check("pen order costs more page turns",
          effort["penOrder"]["pageTurns"] > effort["pageOrder"]["pageTurns"], str(effort))
    check("and one trip back to the start", effort["penOrder"]["returnsToStart"] == 1, str(effort))


def main() -> int:
    run([test_failed_page_lifts_the_pen,
         test_multi_page_waits_for_a_fresh_sheet,
         test_bounds_are_checked_before_a_real_run,
         test_uploaded_paths_are_confined_to_the_uploads_folder,
         test_page_order_finishes_each_page_before_turning,
         test_pen_order_takes_one_pen_through_the_whole_notebook,
         test_the_cost_of_each_order_is_reported])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
