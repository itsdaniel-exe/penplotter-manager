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


def main() -> int:
    run([test_failed_page_lifts_the_pen,
         test_multi_page_waits_for_a_fresh_sheet,
         test_bounds_are_checked_before_a_real_run,
         test_uploaded_paths_are_confined_to_the_uploads_folder])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
