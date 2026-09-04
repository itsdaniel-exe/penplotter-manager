"""Local web console: one interface for input, page/bed setup, preview,
simulate, jog, and run - replacing Inkscape + the 4xiDraw extension + UGS.

Run with:  python -m plotter.server
Then open  http://127.0.0.1:8765
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import gcode as gcode_mod, jobs as jobs_mod
from .config import MachineConfig, PageConfig, TextStyle, PAGE_SIZES
from .fonts import list_fonts
from .simulator import FakeGrblPort
from .stream import GrblStreamer, list_ports

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / "jobs"
UPLOADS_DIR = JOBS_DIR / "uploads"
WEB_DIR = ROOT / "web"
SIMULATOR_PORT = "SIMULATOR"

app = FastAPI(title="Pen Plotter Console")


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
    version = int((WEB_DIR / "app.js").stat().st_mtime)
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


@app.post("/api/upload")
async def api_upload(file: UploadFile):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix.lower()
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    dest.write_bytes(await file.read())
    kind = "svg" if suffix == ".svg" else "doc"
    return {"path": str(dest), "kind": kind, "name": file.filename}


# --------------------------------------------------------------- preview --

class PreviewRequest(BaseModel):
    input: InputIn
    page: PageIn
    style: StyleIn


@app.post("/api/preview")
def api_preview(req: PreviewRequest):
    page = _page_config(req.page)
    style = _style_config(req.style)
    job = jobs_mod.JobInput(text=req.input.text, doc_path=req.input.docPath, svg_path=req.input.svgPath)
    pages = jobs_mod.build_pages(job, page, style)
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
    }


# -------------------------------------------------------------- simulate --

class SimulateRequest(BaseModel):
    input: InputIn
    page: PageIn
    style: StyleIn
    machine: MachineIn = MachineIn()
    bed: BedIn = BedIn()


@app.post("/api/simulate")
def api_simulate(req: SimulateRequest):
    page = _page_config(req.page)
    style = _style_config(req.style)
    machine = _machine_config(req.machine)
    job = jobs_mod.JobInput(text=req.input.text, doc_path=req.input.docPath, svg_path=req.input.svgPath)
    pages = jobs_mod.build_pages(job, page, style)

    results = [jobs_mod.simulate_page(p, page, machine, req.bed.widthMm, req.bed.heightMm) for p in pages]
    return {
        "pageWidthMm": page.width_mm,
        "pageHeightMm": page.height_mm,
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
        try:
            prev.streamer.close()
        except Exception:  # noqa: BLE001 - a dead handle is still worth dropping
            pass
        prev.streamer = None
        return True


def _release_port_ownership(session: Session) -> None:
    global _port_owner
    with _port_owner_lock:
        if _port_owner is session:
            _port_owner = None


def _run_job_blocking(session: Session, machine: MachineConfig, pages, page: PageConfig, q: queue.Queue):
    streamer = session.streamer
    total_pages = len(pages)
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

        try:
            completed = streamer.stream(code, on_progress=on_progress)
        except Exception as e:  # noqa: BLE001 - surface any GRBL/serial error to the UI
            q.put({"type": "error", "message": str(e)})
            return

        warnings = []
        if session.fake_port is not None:
            warnings = session.fake_port.out_of_bounds[:50]
        q.put({
            "type": "pageComplete", "page": pi, "totalPages": total_pages,
            "boundsWarnings": warnings, "cancelled": not completed,
        })

        if not completed:
            q.put({"type": "jobComplete", "cancelled": True})
            return

    q.put({"type": "jobComplete", "cancelled": False})


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()
    session = Session()
    q: queue.Queue = queue.Queue()
    loop = asyncio.get_event_loop()

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
        q.put({"type": "position", "x": pos[0], "y": pos[1], "pen": pen})
        return result

    try:
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")

            if action == "connect":
                port = msg.get("port", SIMULATOR_PORT)
                baud = int(msg.get("baud", 115200))
                bed = msg.get("bed") or {}
                session.bed_width_mm = bed.get("widthMm")
                session.bed_height_mm = bed.get("heightMm")

                # Drop any stale connection this session already had, so
                # clicking Connect twice doesn't strand an open handle.
                if session.streamer is not None:
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

                try:
                    banner = await loop.run_in_executor(None, session.streamer.connect)
                    q.put({"type": "connected", "port": port, "banner": banner, "tookOver": took_over})
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not connect to {port}: {e}"})
                    session.streamer = None
                    _release_port_ownership(session)

            elif action == "disconnect":
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
                await loop.run_in_executor(
                    None, track_position_after, session.streamer.pen_up, cmd, val
                )
            elif action == "penDown" and session.streamer:
                machine = _machine_config(MachineIn(**msg.get("machine", {})))
                cmd, val = machine.pen_down_cmd_value
                await loop.run_in_executor(
                    None, track_position_after, session.streamer.pen_down, cmd, val
                )

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
                await loop.run_in_executor(None, track_position_after, session.streamer.set_zero)
                q.put({"type": "zeroed", "fromX": was[0], "fromY": was[1]})
            elif action == "goZero" and session.streamer:
                was = session.streamer.position
                try:
                    await loop.run_in_executor(None, track_position_after, session.streamer.go_to_zero)
                    q.put({"type": "movedToZero", "fromX": was[0], "fromY": was[1]})
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not return to zero: {e}"})

            elif action == "pause" and session.streamer:
                session.streamer.pause()
            elif action == "resume" and session.streamer:
                session.streamer.resume()
            elif action == "cancel" and session.streamer:
                session.streamer.cancel()

            elif action == "run" and session.streamer:
                page = _page_config(PageIn(**msg["page"]))
                style = _style_config(StyleIn(**msg["style"]))
                machine = _machine_config(MachineIn(**msg.get("machine", {})))
                job = jobs_mod.JobInput(
                    text=msg["input"].get("text"),
                    doc_path=msg["input"].get("docPath"),
                    svg_path=msg["input"].get("svgPath"),
                )
                try:
                    pages = await loop.run_in_executor(None, jobs_mod.build_pages, job, page, style)
                except Exception as e:  # noqa: BLE001
                    q.put({"type": "error", "message": f"Could not build job: {e}"})
                    continue
                q.put({"type": "jobStarted", "totalPages": len(pages)})
                asyncio.get_event_loop().run_in_executor(
                    None, _run_job_blocking, session, machine, pages, page, q
                )

    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()
        if session.streamer:
            try:
                session.streamer.close()
            except Exception:  # noqa: BLE001
                pass
            session.streamer = None
        _release_port_ownership(session)


def main():
    import uvicorn
    JOBS_DIR.mkdir(exist_ok=True)
    uvicorn.run("plotter.server:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
