"""Shared pipeline logic used by both the CLI and the local web app, so
the two never drift: text/file/SVG -> laid-out pages -> gcode -> simulated
or real run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from . import extract, gcode as gcode_mod, svgin
from .autofit import autofit_style
from .config import MachineConfig, PageConfig, TextStyle
from .handwriting import HandStyle
from .layout import PlacedText, layout_text
from .simulator import FakeGrblPort
from .stream import GrblStreamer

_COORD_RE = re.compile(r"([XY])(-?[0-9.]+)")


@dataclass
class JobInput:
    text: str | None = None
    doc_path: str | None = None
    svg_path: str | None = None


def build_pages(
    job: JobInput,
    page: PageConfig,
    style: TextStyle,
    hand: HandStyle | None = None,
    auto_fit: bool = False,
    target_pages: int = 1,
) -> list[PlacedText]:
    """Input -> laid-out pages. `auto_fit` picks the font size and line
    spacing that fill the writing area; `hand` makes the result read as
    handwriting. Neither applies to an SVG, which is placed as drawn."""
    if job.svg_path:
        return [svgin.load_svg_strokes(job.svg_path, page)]
    content = job.text if job.text is not None else extract.extract_text(job.doc_path)
    if auto_fit:
        style = autofit_style(content, page, style, target_pages=target_pages)
    return layout_text(content, page, style, hand)


def resolved_style(
    job: JobInput,
    page: PageConfig,
    style: TextStyle,
    auto_fit: bool = False,
    target_pages: int = 1,
) -> TextStyle:
    """The style actually used for `job` - i.e. `style`, with auto-fit applied
    if requested. Lets the UI show the size it landed on."""
    if not auto_fit or job.svg_path:
        return style
    content = job.text if job.text is not None else extract.extract_text(job.doc_path)
    return autofit_style(content, page, style, target_pages=target_pages)


def bounds_warnings(
    pages: list[PlacedText],
    page: PageConfig,
    machine: MachineConfig,
    bed_width_mm: float | None,
    bed_height_mm: float | None,
) -> list[str]:
    """Machine-space bounds check for a whole job, without needing a board.

    The simulator only reported this for a *simulated* run, so a real Run could
    drive the carriage past the end of its rails and the operator would find out
    by watching it happen. One line per page per axis - the same coordinate
    repeated forty times tells nobody anything.
    """
    if not bed_width_mm and not bed_height_mm:
        return []

    warnings: list[str] = []
    for index, placed in enumerate(pages, start=1):
        code = gcode_mod.strokes_to_gcode(placed, page.height_mm, machine, page.width_mm)
        x = y = 0.0
        xs: list[float] = []
        ys: list[float] = []
        for line in code.splitlines():
            upper = line.upper()
            if not upper.startswith(("G0", "G1")):
                continue
            coords = dict(_COORD_RE.findall(upper))
            x = float(coords.get("X", x))
            y = float(coords.get("Y", y))
            xs.append(x)
            ys.append(y)
        if not xs:
            continue
        if bed_width_mm and (min(xs) < 0 or max(xs) > bed_width_mm):
            warnings.append(
                f"page {index}: X runs {min(xs):.0f} to {max(xs):.0f}mm, "
                f"outside the 0-{bed_width_mm:g}mm work area"
            )
        if bed_height_mm and (min(ys) < 0 or max(ys) > bed_height_mm):
            warnings.append(
                f"page {index}: Y runs {min(ys):.0f} to {max(ys):.0f}mm, "
                f"outside the 0-{bed_height_mm:g}mm work area"
            )
    return warnings


@dataclass
class PageSimResult:
    lines: int
    total_time_s: float
    total_draw_mm: float
    total_travel_mm: float
    bounds_warnings: list[str]
    display_trace: list[dict]
    gcode: str


def simulate_page(
    placed: PlacedText,
    page: PageConfig,
    machine: MachineConfig,
    bed_width_mm: float | None = None,
    bed_height_mm: float | None = None,
) -> PageSimResult:
    """Runs one page's gcode through the real streaming code against a
    fake GRBL board: proves the protocol, reports timing/bounds, and
    produces a page-space (not machine-space) trace for visual playback.
    """
    code = gcode_mod.strokes_to_gcode(placed, page.height_mm, machine, page.width_mm)

    pen_up, pen_down = machine.pen_up_cmd_value, machine.pen_down_cmd_value
    fake = FakeGrblPort(bed_width_mm=bed_width_mm, bed_height_mm=bed_height_mm,
                        pen_up_cmd=pen_up[0], pen_down_cmd=pen_down[0])
    streamer = GrblStreamer(port="SIM", transport=fake)
    streamer.set_pen_mapping(pen_up, pen_down)
    streamer.connect()
    seen = {"n": 0}

    def _progress(prog):
        seen["n"] = prog.line_no

    streamer.stream(code, on_progress=_progress)
    streamer.close()
    result = fake.result()

    # Page-space (y-down-from-top) trace for the animation, independent of
    # whatever invert_y/origin convention the real machine gcode uses.
    display_machine = replace(machine, invert_y=False, invert_x=False, origin_x_mm=0.0, origin_y_mm=0.0)
    display_code = gcode_mod.strokes_to_gcode(placed, page.height_mm, display_machine, page.width_mm)
    dport = FakeGrblPort(pen_up_cmd=pen_up[0], pen_down_cmd=pen_down[0])
    dstreamer = GrblStreamer(port="SIM", transport=dport)
    dstreamer.set_pen_mapping(pen_up, pen_down)
    dstreamer.connect()
    dstreamer.stream(display_code)
    dstreamer.close()

    return PageSimResult(
        lines=seen["n"],
        total_time_s=result.total_time_s,
        total_draw_mm=result.total_draw_mm,
        total_travel_mm=result.total_travel_mm,
        bounds_warnings=result.out_of_bounds,
        display_trace=[
            # 2dp is ~10x finer than the pen resolves; full float repr roughly
            # doubles the JSON shipped to the browser for a long job.
            {"t": round(tp.t, 3), "x": round(tp.x, 2), "y": round(tp.y, 2), "pen": tp.pen_down}
            for tp in dport.trace
        ],
        gcode=code,
    )
