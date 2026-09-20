"""Local web console: one interface for input, page/bed setup, preview,
simulate, jog, and run - replacing Inkscape + the 4xiDraw extension + UGS.

Run with:  python -m plotter.server
Then open  http://127.0.0.1:8765
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import gcode as gcode_mod, jobs as jobs_mod, notebook as notebook_mod
from .config import MachineConfig, PageConfig, TextStyle, PAGE_SIZES
from . import extract
from .fonts import list_fonts
from .handwriting import HandStyle
from .layout import PlacedText
from .simulator import FakeGrblPort
from .stream import GrblStreamer, list_ports

from . import __version__
from .paths import APP_NAME, data_dir, is_frozen, jobs_dir, web_dir

JOBS_DIR = jobs_dir()
UPLOADS_DIR = JOBS_DIR / "uploads"
WEB_DIR = web_dir()
SIMULATOR_PORT = "SIMULATOR"

app = FastAPI(title=APP_NAME)

# Where the packaged app publishes its builds. Used only to tell the operator
# a newer version exists - nothing downloads or installs itself.
RELEASES_API = "https://api.github.com/repos/itsdaniel-exe/penplotter-manager/releases/latest"
RELEASES_PAGE = "https://github.com/itsdaniel-exe/penplotter-manager/releases"

# Server-side log lines kept in memory for the diagnostics dump: "it broke" by
# text message is not something anyone can debug.
_LOG_RING: deque[str] = deque(maxlen=500)


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_RING.append(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never break the app
            pass


def install_log_capture() -> None:
    """Keep the last few hundred log lines so diagnostics can include them."""
    handler = _RingHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    if not any(isinstance(h, _RingHandler) for h in root.handlers):
        root.addHandler(handler)
        root.setLevel(logging.INFO)


# ---------------------------------------------------------------- models --

class PageIn(BaseModel):
    widthMm: float = 195.0
    heightMm: float = 280.0
    marginTopMm: float = 15.0
    marginBottomMm: float = 15.0
    marginLeftMm: float = 12.0
    marginRightMm: float = 12.0


class StyleIn(BaseModel):
    font: str = "HersheySansMed"
    fontSizeMm: float = 5.0
    lineSpacingMm: float = 8.0
    align: Literal["left", "center", "right"] = "left"
    autoFit: bool = False
    targetPages: int = 1


class HandIn(BaseModel):
    """Handwriting realism. `seed` keeps a page reproducible, so Preview,
    Simulate and the real Run all draw the identical page."""
    enabled: bool = True
    amount: float = 1.0
    seed: int = 7


class InputIn(BaseModel):
    text: Optional[str] = None
    docPath: Optional[str] = None
    svgPath: Optional[str] = None


class MachineIn(BaseModel):
    servoUp: int = 10
    servoDown: int = 50
    travelFeed: int = 10000
    drawFeed: int = 2500
    invertY: bool = False
    invertX: bool = True
    swapPen: bool = True
    originXMm: float = 0.0
    originYMm: float = 0.0


class NotebookIn(BaseModel):
    """The physical notebook, measured with a ruler. Defaults are a standard
    A4 ruled pad: 8mm ruling, 24 usable lines, printed margin at 25mm."""
    pageWidthMm: float = 210.0
    pageHeightMm: float = 297.0
    marginLeftMm: float = 25.0
    marginRightMm: float = 12.0
    firstLineMm: float = 30.0
    lineSpacingMm: float = 8.0
    linesPerPage: int = 24


class PenPassIn(BaseModel):
    """One pen's lines. `docPath` is an uploaded document; `text` is typed."""
    name: str = "Pen"
    colour: str = "#1b1b1b"
    docPath: Optional[str] = None
    text: Optional[str] = None


class BedIn(BaseModel):
    widthMm: Optional[float] = None
    heightMm: Optional[float] = None


def _page_config(p: PageIn) -> PageConfig:
    return PageConfig(
        width_mm=p.widthMm, height_mm=p.heightMm,
        margin_top_mm=p.marginTopMm, margin_bottom_mm=p.marginBottomMm,
        margin_left_mm=p.marginLeftMm, margin_right_mm=p.marginRightMm,
    )


def _style_config(s: StyleIn) -> TextStyle:
    return TextStyle(font=s.font, font_size_mm=s.fontSizeMm, line_spacing_mm=s.lineSpacingMm, align=s.align)


def _hand_style(h: HandIn | None) -> HandStyle:
    if h is None:
        return HandStyle(enabled=False)
    return HandStyle(enabled=h.enabled, amount=h.amount, seed=h.seed)


def _machine_config(m: MachineIn) -> MachineConfig:
    return MachineConfig(servo_up=m.servoUp, servo_down=m.servoDown,
                          travel_feed=m.travelFeed, draw_feed=m.drawFeed,
                          invert_y=m.invertY, invert_x=m.invertX, swap_pen=m.swapPen,
                          origin_x_mm=m.originXMm, origin_y_mm=m.originYMm)


# --------------------------------------------------------------- static ---

@app.get("/")
def index():
    """Serve the console with a cache-busting stamp on app.js.

    Without this the browser happily keeps an old app.js after an edit, so
    a fix looks like it silently didn't work - which cost real debugging
    time against live hardware once already.
    """
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    # The packaged app unpacks to a fresh temp folder each launch, so mtime is
    # useless there - the app version is what changes between builds.
    version = __version__ if is_frozen() else int((WEB_DIR / "app.js").stat().st_mtime)
    html = html.replace("/static/app.js", f"/static/app.js?v={version}")
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ------------------------------------------------------------------ info --

@app.get("/api/fonts")
def api_fonts():
    return {"fonts": list_fonts()}


@app.get("/api/page-sizes")
def api_page_sizes():
    return {"sizes": {k: {"widthMm": w, "heightMm": h} for k, (w, h) in PAGE_SIZES.items()}}


@app.get("/api/ports")
def api_ports():
    return {"ports": [SIMULATOR_PORT] + list_ports()}


# Uploads are working copies of files the operator already has. Keeping every
# one forever grows jobs/uploads without bound and without anyone looking.
UPLOAD_KEEP = 40


def _prune_uploads(keep: int = UPLOAD_KEEP) -> None:
    try:
        files = sorted(UPLOADS_DIR.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
        for stale in files[keep:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass  # housekeeping must never fail an upload


@app.post("/api/upload")
async def api_upload(file: UploadFile):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix.lower()
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    dest.write_bytes(await file.read())
    _prune_uploads()
    kind = "svg" if suffix == ".svg" else "doc"
    return {"path": str(dest), "kind": kind, "name": file.filename}


def _safe_upload_path(raw: Optional[str]) -> Optional[str]:
    """Only files this console itself stored under jobs/uploads.

    The browser hands back a *server-side* path, so without this check a
    hand-made request could name any file the server account can read and have
    its contents laid out and plotted.
    """
    if not raw:
        return None
    resolved = Path(raw).resolve()
    uploads = UPLOADS_DIR.resolve()
    if uploads not in resolved.parents:
        raise HTTPException(status_code=400, detail="That file was not uploaded to this console.")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="That uploaded file is gone - upload it again.")
    return str(resolved)


def _job_input(i: InputIn) -> jobs_mod.JobInput:
    return jobs_mod.JobInput(
        text=i.text,
        doc_path=_safe_upload_path(i.docPath),
        svg_path=_safe_upload_path(i.svgPath),
    )


def _as_http_error(e: Exception) -> HTTPException:
    """Bad input (an unreadable SVG, a zero font size, an unsupported document)
    is the operator's problem to fix, so say what happened instead of letting
    it surface as a bare "Internal Server Error"."""
    if isinstance(e, HTTPException):
        return e
    return HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")


# ------------------------------------------------------------ app support --

@app.get("/api/app-info")
def api_app_info():
    return {
        "name": APP_NAME,
        "version": __version__,
        "packaged": is_frozen(),
        "dataDir": str(data_dir()),
        "releasesPage": RELEASES_PAGE,
    }


@app.get("/api/update-check")
def api_update_check():
    """Ask GitHub whether a newer build has been published.

    Deliberately passive: it reports, it never downloads or installs. Any
    failure (offline, no releases yet, rate limited) is not an error worth
    interrupting someone mid-job for.
    """
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                            "User-Agent": f"{APP_NAME}/{__version__}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return {"checked": False, "current": __version__, "reason": str(e)}

    latest = str(data.get("tag_name") or "").lstrip("vV")
    return {
        "checked": True,
        "current": __version__,
        "latest": latest,
        "updateAvailable": bool(latest) and _version_tuple(latest) > _version_tuple(__version__),
        "url": data.get("html_url") or RELEASES_PAGE,
        "notes": (data.get("body") or "")[:400],
    }


def _version_tuple(v: str) -> tuple:
    parts = []
    for chunk in v.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


class DiagnosticsRequest(BaseModel):
    uiLog: str = ""
    settings: dict = {}
    note: str = ""


@app.post("/api/diagnostics")
def api_diagnostics(req: DiagnosticsRequest):
    """Write everything needed to debug a complaint into one text file.

    The machine is at someone else's desk; "it's not working" over a text
    message is not something anyone can act on.
    """
    folder = data_dir() / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    dest = folder / f"penplotter-diagnostics-{stamp}.txt"

    lines = [
        f"{APP_NAME} {__version__}",
        f"packaged: {is_frozen()}",
        f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"windows: {platform.platform()}",
        f"python: {sys.version.split()[0]}",
        f"data dir: {data_dir()}",
        f"serial ports seen: {', '.join(list_ports()) or '(none)'}",
        "",
        "--- what the operator said ---",
        req.note or "(nothing written)",
        "",
        "--- settings ---",
        json.dumps(req.settings, indent=2, default=str),
        "",
        "--- console log ---",
        req.uiLog or "(empty)",
        "",
        "--- server log ---",
        "\n".join(_LOG_RING) or "(empty)",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return {"path": str(dest), "folder": str(folder)}


@app.post("/api/open-releases")
def api_open_releases():
    """Open the downloads page in the operator's normal browser."""
    try:
        os.startfile(RELEASES_PAGE)  # noqa: S606 - fixed, in-repo URL
    except (AttributeError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Could not open the page: {e}") from e
    return {"opened": RELEASES_PAGE}


@app.post("/api/open-folder")
def api_open_folder():
    """Open the diagnostics folder in Explorer so the file can be attached."""
    folder = data_dir() / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(folder))  # noqa: S606 - Windows shell open, fixed path
    except (AttributeError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"Could not open the folder: {e}") from e
    return {"opened": str(folder)}


# -------------------------------------------------------------- notebook --

def _notebook_config(n: NotebookIn) -> notebook_mod.NotebookConfig:
    return notebook_mod.NotebookConfig(
        page_width_mm=n.pageWidthMm, page_height_mm=n.pageHeightMm,
        margin_left_mm=n.marginLeftMm, margin_right_mm=n.marginRightMm,
        first_line_mm=n.firstLineMm, line_spacing_mm=n.lineSpacingMm,
        lines_per_page=n.linesPerPage,
    )


def _pen_passes(passes: list[PenPassIn]) -> list[notebook_mod.PenPass]:
    """Read each pen's document into lines, keeping its own line breaks.

    The documents are already wrapped to the page they were written for, and
    those breaks are what keeps the pens aligned - re-flowing them would put
    the headings against the wrong body text.
    """
    out = []
    for p in passes:
        if p.docPath:
            text = extract.extract_text(_safe_upload_path(p.docPath))
        else:
            text = p.text or ""
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out.append(notebook_mod.PenPass(name=p.name, colour=p.colour, lines=lines))
    return out


# Building 100+ notebook pages takes a couple of seconds, and the console asks
# for them one at a time while the operator pages through the preview.
_notebook_cache: dict[str, tuple] = {}


def _notebook_pages(req: "NotebookRequest"):
    key = req.model_dump_json(exclude={"page"})
    cached = _notebook_cache.get(key)
    if cached is not None:
        return cached

    passes = _pen_passes(req.passes)
    if not any(any(line.strip() for line in p.lines) for p in passes):
        raise HTTPException(status_code=400, detail="None of those files had any text in them.")

    pages, warnings = notebook_mod.build_notebook_pages(
        passes,
        _notebook_config(req.notebook),
        _style_config(req.style),
        _hand_style(req.hand),
        auto_fit=req.style.autoFit,
    )
    size_mm = notebook_mod.fit_font_size(passes, _notebook_config(req.notebook),
                                         _style_config(req.style)) if req.style.autoFit \
        else req.style.fontSizeMm
    result = (pages, warnings, size_mm)
    _notebook_cache.clear()  # one job at a time; this is a single-operator console
    _notebook_cache[key] = result
    return result


class NotebookRequest(BaseModel):
    passes: list[PenPassIn]
    notebook: NotebookIn = NotebookIn()
    style: StyleIn = StyleIn()
    hand: HandIn = HandIn()
    page: int = 0


@app.post("/api/notebook/preview")
def api_notebook_preview(req: NotebookRequest):
    """One page at a time: a full record is 100+ pages and a quarter of a
    million strokes, which is not something to hand a browser in one go."""
    try:
        pages, warnings, size_mm = _notebook_pages(req)
    except Exception as e:  # noqa: BLE001
        raise _as_http_error(e) from e

    if not pages:
        raise HTTPException(status_code=400, detail="That document produced no pages.")

    index = max(0, min(req.page, len(pages) - 1))
    page = pages[index]
    nb = _notebook_config(req.notebook)
    return {
        "pageCount": len(pages),
        "pageIndex": index,
        "firstLineNo": page.first_line_no,
        "lastLineNo": page.last_line_no,
        "pageWidthMm": nb.page_width_mm,
        "pageHeightMm": nb.page_height_mm,
        "marginLeftMm": nb.margin_left_mm,
        "marginRightMm": nb.margin_right_mm,
        "firstLineMm": nb.first_line_mm,
        "lineSpacingMm": nb.line_spacing_mm,
        "linesPerPage": nb.lines_per_page,
        "fontSizeMm": size_mm,
        "warnings": warnings[:10],
        "passes": [
            {
                "name": p.name,
                "colour": p.colour,
                "writtenLines": p.written_lines,
                "strokes": [[[round(x, 3), round(y, 3)] for x, y in s] for s in p.strokes],
            }
            for p in page.passes
        ],
        "totals": [
            {"name": name, "lines": sum(pp.written_lines for pg in pages for pp in pg.passes
                                        if pp.name == name)}
            for name in [p.name for p in pages[0].passes]
        ],
    }


# --------------------------------------------------------------- preview --

class PreviewRequest(BaseModel):
    input: InputIn
    page: PageIn
    style: StyleIn
    hand: HandIn = HandIn()


@app.post("/api/preview")
def api_preview(req: PreviewRequest):
    page = _page_config(req.page)
    style = _style_config(req.style)
    hand = _hand_style(req.hand)
    job = _job_input(req.input)
    try:
        used = jobs_mod.resolved_style(job, page, style, req.style.autoFit, req.style.targetPages)
        pages = jobs_mod.build_pages(job, page, style, hand, req.style.autoFit, req.style.targetPages)
    except Exception as e:  # noqa: BLE001
        raise _as_http_error(e) from e
    return {
        "pages": [
            {"strokes": [[[round(x, 3), round(y, 3)] for x, y in s] for s in p.strokes]}
            for p in pages
        ],
        "pageWidthMm": page.width_mm,
        "pageHeightMm": page.height_mm,
        "marginTopMm": page.margin_top_mm,
        "marginBottomMm": page.margin_bottom_mm,
        "marginLeftMm": page.margin_left_mm,
        "marginRightMm": page.margin_right_mm,
        # so the UI can show what auto-fit actually chose
        "fontSizeMm": used.font_size_mm,
        "lineSpacingMm": used.line_spacing_mm,
        # characters the font can't draw, SVG elements that were skipped, etc.
        "warnings": [w for p in pages for w in p.warnings][:10],
    }


# -------------------------------------------------------------- simulate --

class SimulateRequest(BaseModel):
    input: InputIn
    page: PageIn
    style: StyleIn
    machine: MachineIn = MachineIn()
    bed: BedIn = BedIn()
    hand: HandIn = HandIn()


@app.post("/api/simulate")
def api_simulate(req: SimulateRequest):
    page = _page_config(req.page)
    style = _style_config(req.style)
    machine = _machine_config(req.machine)
    hand = _hand_style(req.hand)
    job = _job_input(req.input)
    try:
        used = jobs_mod.resolved_style(job, page, style, req.style.autoFit, req.style.targetPages)
        pages = jobs_mod.build_pages(job, page, style, hand, req.style.autoFit, req.style.targetPages)
        results = [jobs_mod.simulate_page(p, page, machine, req.bed.widthMm, req.bed.heightMm) for p in pages]
    except Exception as e:  # noqa: BLE001
        raise _as_http_error(e) from e
    return {
        "pageWidthMm": page.width_mm,
        "pageHeightMm": page.height_mm,
        "fontSizeMm": used.font_size_mm,
        "lineSpacingMm": used.line_spacing_mm,
        "pages": [
            {
                "lines": r.lines,
                "totalTimeS": r.total_time_s,
                "totalDrawMm": r.total_draw_mm,
                "totalTravelMm": r.total_travel_mm,
                "boundsWarnings": r.bounds_warnings[:200],
                "trace": r.display_trace,
            }
            for r in results
        ],
    }


# --------------------------------------------------- live session (ws) ---

class Session:
    def __init__(self):
        self.streamer: GrblStreamer | None = None
        self.fake_port: FakeGrblPort | None = None
        self.bed_width_mm: float | None = None
        self.bed_height_mm: float | None = None
        self.lock = threading.Lock()
        self.job: asyncio.Future | None = None
        # Set by the operator confirming a fresh sheet between pages.
        self.page_ready = threading.Event()
        # The session's outbound queue, so another tab taking the port can
        # tell this one it has been disconnected instead of leaving it
        # showing "connected" with every button silently doing nothing.
        self.q: queue.Queue | None = None

    @property
    def job_running(self) -> bool:
        return self.job is not None and not self.job.done()


# Motion and servo commands, which must never interleave with a running job.
_BLOCKED_WHILE_RUNNING = {"jog", "penUp", "penDown", "zero", "goZero", "home", "unlock"}


# Windows allows exactly one open handle on a COM port - a second one fails
# with "Access is denied". Every browser tab (and every stale one left open)
# gets its own Session, so without this the first tab to connect silently
# owns the machine and every other tab is stuck. Hand the port to whoever
# asked most recently instead, and drop the previous holder.
_port_owner_lock = threading.Lock()
_port_owner: Session | None = None


def _take_port_ownership(session: Session) -> bool:
    """Make `session` the sole owner of the real serial port. Returns True if
    a previous owner had to be disconnected."""
    global _port_owner
    with _port_owner_lock:
        prev = _port_owner
        _port_owner = session
        if prev is None or prev is session or prev.streamer is None:
            return False
        # Closing the port under a running job strands the pen on the paper
        # and leaves the job thread erroring into a dead socket. Stop it
        # properly first - cancel() lets the machine finish its buffered moves
        # and _run_job_blocking lifts the pen.
        if prev.job_running:
            try:
                prev.streamer.cancel()
                prev.job.result(timeout=45)
            except Exception:  # noqa: BLE001
                pass
        try:
            prev.streamer.close()
        except Exception:  # noqa: BLE001 - a dead handle is still worth dropping
            pass
        prev.streamer = None
        if prev.q is not None:
            prev.q.put({"type": "portTakenOver"})
        return True


def _release_port_ownership(session: Session) -> None:
    global _port_owner
    with _port_owner_lock:
        if _port_owner is session:
            _port_owner = None


def _run_job_blocking(session: Session, machine: MachineConfig, pages, page: PageConfig, q: queue.Queue):
    streamer = session.streamer
    total_pages = len(pages)
    # This machine lifts with M03 (swap_pen), so the streamer has to be told
    # which command means down or its live pen readout runs inverted.
    streamer.set_pen_mapping(machine.pen_up_cmd_value, machine.pen_down_cmd_value)

    def lift_pen(why: str) -> None:
        try:
            streamer.pen_up(*machine.pen_up_cmd_value)
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "message": f"{why}, but couldn't lift the pen: {e}"})
        pos = streamer.position
        q.put({"type": "position", "x": pos[0], "y": pos[1],
               "pen": streamer.is_pen_down, "penKnown": streamer.is_pen_known})

    for pi, placed in enumerate(pages, start=1):
        code = gcode_mod.strokes_to_gcode(placed, page.height_mm, machine, page.width_mm)
        lines = [ln for ln in code.splitlines() if ln.strip()]
        total_lines = len(lines)
        last_sent = 0.0

        def on_progress(prog, pi=pi):
            nonlocal last_sent
            t = time.time()
            if prog.line_no < prog.total_lines and (t - last_sent) < 0.08:
                return
            last_sent = t
            pos = streamer.position
            q.put({
                "type": "progress",
                "page": pi, "totalPages": total_pages,
                "line": prog.line_no, "totalLines": prog.total_lines,
                "x": pos[0], "y": pos[1], "pen": streamer.is_pen_down,
            })

        # Only this page's warnings: the simulator port keeps appending for
        # the life of the session, so page 3 used to re-report page 1's.
        warn_start = len(session.fake_port.out_of_bounds) if session.fake_port is not None else 0

        try:
            completed = streamer.stream(code, on_progress=on_progress)
        except Exception as e:  # noqa: BLE001 - surface any GRBL/serial error to the UI
            # A mid-page failure used to return here, leaving the pen sitting
            # on the paper bleeding a blot, and the UI stuck on "Running".
            q.put({"type": "error", "message": str(e)})
            lift_pen("The job stopped")
            q.put({"type": "jobComplete", "cancelled": True})
            return

        if not completed:
            # Cancel can land mid-stroke - don't leave the pen on the paper.
            lift_pen("Stopped")

        warnings = []
        if session.fake_port is not None:
            warnings = session.fake_port.out_of_bounds[warn_start:][:50]
        q.put({
            "type": "pageComplete", "page": pi, "totalPages": total_pages,
            "boundsWarnings": warnings, "cancelled": not completed,
        })

        if not completed:
            q.put({"type": "jobComplete", "cancelled": True})
            return

        # Every page is drawn from the same zero, so without stopping here
        # page 2 goes straight on top of page 1 on the same sheet.
        if pi < total_pages:
            lift_pen("Page finished")
            session.page_ready.clear()
            q.put({"type": "pageWait", "page": pi, "totalPages": total_pages})
            while not session.page_ready.wait(0.2):
                if streamer.is_cancelled:
                    q.put({"type": "jobComplete", "cancelled": True})
                    return

    q.put({"type": "jobComplete", "cancelled": False})


def _notebook_page_config(nb: notebook_mod.NotebookConfig) -> PageConfig:
    """The notebook page, described the way the gcode generator expects."""
    return PageConfig(width_mm=nb.page_width_mm, height_mm=nb.page_height_mm,
                      margin_top_mm=0.0, margin_bottom_mm=0.0,
                      margin_left_mm=nb.margin_left_mm, margin_right_mm=nb.margin_right_mm)


def _run_notebook_blocking(session: Session, machine: MachineConfig,
                           pages: list, nb: notebook_mod.NotebookConfig, q: queue.Queue):
    """Write a notebook: for each page, each pen in turn, then wait.

    The operator is part of this loop - they swap the pen and turn the page -
    so the thread blocks on them rather than running ahead. Everything is
    written from the same zero: the notebook does not move between pages.
    """
    streamer = session.streamer
    streamer.set_pen_mapping(machine.pen_up_cmd_value, machine.pen_down_cmd_value)
    page_config = _notebook_page_config(nb)
    total_pages = len(pages)
    current_pen: str | None = None

    def lift_pen(why: str) -> None:
        try:
            streamer.pen_up(*machine.pen_up_cmd_value)
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "message": f"{why}, but couldn't lift the pen: {e}"})
        pos = streamer.position
        q.put({"type": "position", "x": pos[0], "y": pos[1],
               "pen": streamer.is_pen_down, "penKnown": streamer.is_pen_known})

    def wait_for_operator(message: dict) -> bool:
        """Block until the operator confirms. False if they cancelled instead."""
        session.page_ready.clear()
        q.put(message)
        while not session.page_ready.wait(0.2):
            if streamer.is_cancelled:
                return False
        return True

    for page in pages:
        for placed in page.passes:
            if not placed.strokes:
                continue

            if placed.name != current_pen:
                lift_pen("Pen change")
                if not wait_for_operator({
                    "type": "penChange", "pen": placed.name, "colour": placed.colour,
                    "page": page.index + 1, "totalPages": total_pages,
                }):
                    q.put({"type": "jobComplete", "cancelled": True})
                    return
                current_pen = placed.name

            code = gcode_mod.strokes_to_gcode(
                PlacedText(strokes=placed.strokes, page_index=page.index),
                nb.page_height_mm, machine, nb.page_width_mm,
            )
            total_lines = len([ln for ln in code.splitlines() if ln.strip()])
            last_sent = 0.0

            def on_progress(prog, page=page, placed=placed):
                nonlocal last_sent
                now = time.time()
                if prog.line_no < prog.total_lines and (now - last_sent) < 0.08:
                    return
                last_sent = now
                pos = streamer.position
                q.put({
                    "type": "progress",
                    "page": page.index + 1, "totalPages": total_pages,
                    "line": prog.line_no, "totalLines": prog.total_lines,
                    "pen": placed.name,
                    "x": pos[0], "y": pos[1], "penDown": streamer.is_pen_down,
                })

            try:
                completed = streamer.stream(code, on_progress=on_progress)
            except Exception as e:  # noqa: BLE001
                q.put({"type": "error", "message": str(e)})
                lift_pen("The job stopped")
                q.put({"type": "jobComplete", "cancelled": True})
                return

            if not completed:
                lift_pen("Stopped")
                q.put({"type": "jobComplete", "cancelled": True})
                return

            q.put({"type": "passComplete", "page": page.index + 1, "totalPages": total_pages,
                   "pen": placed.name, "lines": placed.written_lines})

        lift_pen("Page finished")
        q.put({"type": "pageComplete", "page": page.index + 1, "totalPages": total_pages,
               "boundsWarnings": [], "cancelled": False})

        if page.index + 1 < total_pages:
            if not wait_for_operator({"type": "pageWait", "page": page.index + 1,
                                      "totalPages": total_pages, "notebook": True}):
                q.put({"type": "jobComplete", "cancelled": True})
                return

    q.put({"type": "jobComplete", "cancelled": False})


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()
    session = Session()
    q: queue.Queue = queue.Queue()
    loop = asyncio.get_event_loop()

    # so a tab that loses the port to another tab can be told about it
    session.q = q

    async def sender():
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item is None:
                return
            await websocket.send_json(item)

    sender_task = asyncio.create_task(sender())

    def track_position_after(fn, *args, **kwargs):
        result = fn(*args, **kwargs)
        pos = session.streamer.position if session.streamer else (0.0, 0.0)
        pen = session.streamer.is_pen_down if session.streamer else False
        known = session.streamer.is_pen_known if session.streamer else False
        q.put({"type": "position", "x": pos[0], "y": pos[1], "pen": pen, "penKnown": known})
        return result

    async def stop_job():
        """Cancel a running job and wait for the pen to lift, so letting go
        of the port mid-job doesn't strand the pen on the paper."""
        if session.job is None or session.job.done() or session.streamer is None:
            return
        session.streamer.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(session.job), timeout=40)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001
            pass

    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")

            # Anything that moves the machine has to stay out of the way of a
            # running job: two threads writing the same serial port interleave
            # their lines, and a stray G92 or jog lands in the middle of the
            # page being drawn.
            if action in _BLOCKED_WHILE_RUNNING and session.job_running:
                q.put({"type": "error",
                       "message": "Not while a job is running - Pause or Cancel it first."})
                continue

            if action == "connect":
                port = msg.get("port", SIMULATOR_PORT)
                baud = int(msg.get("baud", 115200))
                bed = msg.get("bed") or {}
                session.bed_width_mm = bed.get("widthMm")
                session.bed_height_mm = bed.get("heightMm")

                # Drop any stale connection this session already had, so
                # clicking Connect twice doesn't strand an open handle.
                if session.streamer is not None:
                    await stop_job()
                    try:
                        await loop.run_in_executor(None, session.streamer.close)
                    except Exception:  # noqa: BLE001
                        pass
                    session.streamer = None

                if port == SIMULATOR_PORT:
                    # speed_factor: a live "Run" plays at 15x real speed
                    # instead of completing instantly, so pause/resume/
                    # cancel have an actual window to be used and the live
                    # position readout visibly moves - unlike the fast,
                    # instant bulk simulation /api/simulate uses for stats.
                    _release_port_ownership(session)
                    session.fake_port = FakeGrblPort(session.bed_width_mm, session.bed_height_mm, speed_factor=15.0)
                    session.streamer = GrblStreamer(port=port, baud=baud, transport=session.fake_port)
                    took_over = False
                else:
                    session.fake_port = None
                    took_over = await loop.run_in_executor(None, _take_port_ownership, session)
                    session.streamer = GrblStreamer(port=port, baud=baud)

                # Tell the streamer which servo command lowers the pen, so the
                # live pen readout isn't inverted on a swap_pen machine.
                if msg.get("machine"):
                    mapping = _machine_config(MachineIn(**msg["machine"]))
                    session.streamer.set_pen_mapping(
                        mapping.pen_up_cmd_value, mapping.pen_down_cmd_value
                    )

                try:
                    banner = await loop.run_in_executor(None, session.streamer.connect)
                    q.put({"type": "connected", "port": port, "banner": banner, "tookOver": took_over})
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not connect to {port}: {e}"})
                    session.streamer = None
                    _release_port_ownership(session)

            elif action == "disconnect":
                await stop_job()
                if session.streamer:
                    await loop.run_in_executor(None, session.streamer.close)
                    session.streamer = None
                _release_port_ownership(session)
                q.put({"type": "disconnected"})

            elif action == "jog" and session.streamer:
                dx, dy = float(msg.get("dx", 0)), float(msg.get("dy", 0))
                feed = int(msg.get("feed", 3000))
                try:
                    await loop.run_in_executor(None, track_position_after, session.streamer.jog, dx, dy, feed)
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": str(e)})

            elif action == "penUp" and session.streamer:
                machine = _machine_config(MachineIn(**msg.get("machine", {})))
                cmd, val = machine.pen_up_cmd_value
                try:
                    await loop.run_in_executor(
                        None, track_position_after, session.streamer.pen_up, cmd, val
                    )
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Pen up failed: {e}"})
            elif action == "penDown" and session.streamer:
                machine = _machine_config(MachineIn(**msg.get("machine", {})))
                cmd, val = machine.pen_down_cmd_value
                try:
                    await loop.run_in_executor(
                        None, track_position_after, session.streamer.pen_down, cmd, val
                    )
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Pen down failed: {e}"})

            elif action == "unlock" and session.streamer:
                # GRBL boots into Alarm when homing is enabled ($22=1), and
                # trips into it on a soft limit - every motion command is
                # refused until '$X' clears it.
                try:
                    await loop.run_in_executor(None, session.streamer.unlock)
                    q.put({"type": "unlocked"})
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Unlock failed: {e}"})
            elif action == "home" and session.streamer:
                try:
                    await loop.run_in_executor(None, track_position_after, session.streamer.home)
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Homing failed: {e}"})
            elif action == "zero" and session.streamer:
                # Neither of these moves anything visible when you're already
                # at zero, so confirm them explicitly - otherwise a working
                # button is indistinguishable from a dead one.
                was = session.streamer.position
                try:
                    await loop.run_in_executor(None, track_position_after, session.streamer.set_zero)
                    q.put({"type": "zeroed", "fromX": was[0], "fromY": was[1]})
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not set zero: {e}"})
            elif action == "goZero" and session.streamer:
                was = session.streamer.position
                try:
                    await loop.run_in_executor(None, track_position_after, session.streamer.go_to_zero)
                    q.put({"type": "movedToZero", "fromX": was[0], "fromY": was[1]})
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not return to zero: {e}"})

            elif action in ("pause", "resume", "cancel") and session.streamer:
                try:
                    getattr(session.streamer, action)()
                except Exception as e:  # noqa: BLE001 - a dead port must not kill the socket
                    q.put({"type": "error", "message": f"{action.capitalize()} failed: {e}"})

            elif action in ("nextPage", "continueJob"):
                # Operator has changed the pen or loaded a fresh sheet.
                session.page_ready.set()

            elif action == "runNotebook" and session.streamer:
                if session.job_running:
                    q.put({"type": "error",
                           "message": "A job is already running - Cancel it before starting another."})
                    continue
                try:
                    req = NotebookRequest(**msg["notebookJob"])
                    pages, warnings, _ = await loop.run_in_executor(None, _notebook_pages, req)
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not build the notebook job: {e}"})
                    continue

                machine = _machine_config(MachineIn(**msg.get("machine", {})))
                nb = _notebook_config(req.notebook)

                if not msg.get("force"):
                    page_config = _notebook_page_config(nb)
                    sample = [PlacedText(strokes=[st for pp in pg.passes for st in pp.strokes],
                                         page_index=pg.index)
                              for pg in pages[:1]]
                    bounds = await loop.run_in_executor(
                        None, jobs_mod.bounds_warnings, sample, page_config, machine,
                        session.bed_width_mm, session.bed_height_mm,
                    )
                    if bounds:
                        q.put({"type": "boundsBlocked", "warnings": bounds[:20],
                               "totalPages": len(pages)})
                        continue

                for w in warnings:
                    q.put({"type": "error", "message": w})
                q.put({"type": "jobStarted", "totalPages": len(pages), "notebook": True})
                session.job = loop.run_in_executor(
                    None, _run_notebook_blocking, session, machine, pages, nb, q
                )

            elif action == "run" and session.streamer:
                if session.job_running:
                    q.put({"type": "error",
                           "message": "A job is already running - Cancel it before starting another."})
                    continue
                page = _page_config(PageIn(**msg["page"]))
                style = _style_config(StyleIn(**msg["style"]))
                machine = _machine_config(MachineIn(**msg.get("machine", {})))
                style_in = StyleIn(**msg["style"])
                hand = _hand_style(HandIn(**msg["hand"]) if msg.get("hand") else None)
                job = _job_input(InputIn(**msg["input"]))
                try:
                    # Same arguments the preview used, so the pen draws exactly
                    # the page that was previewed - the handwriting jitter is
                    # seeded, not re-rolled here.
                    pages = await loop.run_in_executor(
                        None, jobs_mod.build_pages, job, page, style,
                        hand, style_in.autoFit, style_in.targetPages,
                    )
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not build job: {e}"})
                    continue

                # The simulator was the only thing checking this, so a real run
                # could drive the carriage past the end of its rails.
                if not msg.get("force"):
                    warnings = await loop.run_in_executor(
                        None, jobs_mod.bounds_warnings, pages, page, machine,
                        session.bed_width_mm, session.bed_height_mm,
                    )
                    if warnings:
                        q.put({"type": "boundsBlocked", "warnings": warnings[:20],
                               "totalPages": len(pages)})
                        continue

                q.put({"type": "jobStarted", "totalPages": len(pages)})
                session.job = loop.run_in_executor(
                    None, _run_job_blocking, session, machine, pages, page, q
                )

    except WebSocketDisconnect:
        pass
    finally:
        # Cancelling the task doesn't unblock the thread sitting in q.get(),
        # so without this every closed tab leaks a worker thread for good.
        await stop_job()
        q.put(None)
        sender_task.cancel()
        if session.streamer:
            try:
                session.streamer.close()
            except Exception:  # noqa: BLE001
                pass
            session.streamer = None
        _release_port_ownership(session)


def main(port: int = 8765):
    import uvicorn
    install_log_capture()
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    # The app object, not an import string: a frozen build has no importable
    # "plotter.server" module path for uvicorn to re-import.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
