"""Shared pipeline logic used by both the CLI and the local web app, so
the two never drift: text/file/SVG -> laid-out pages -> gcode -> simulated
or real run.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from . import extract, gcode as gcode_mod, svgin
from .autofit import autofit_style
from .config import MachineConfig, PageConfig, TextStyle
from .handwriting import HandStyle
from .layout import PlacedText, layout_text
from .simulator import FakeGrblPort
from .stream import GrblStreamer


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

    fake = FakeGrblPort(bed_width_mm=bed_width_mm, bed_height_mm=bed_height_mm)
    streamer = GrblStreamer(port="SIM", transport=fake)
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
    dport = FakeGrblPort()
    dstreamer = GrblStreamer(port="SIM", transport=dport)
    dstreamer.connect()
    dstreamer.stream(display_code)
    dstreamer.close()

    return PageSimResult(
        lines=seen["n"],
        total_time_s=result.total_time_s,
        total_draw_mm=result.total_draw_mm,
        total_travel_mm=result.total_travel_mm,
        bounds_warnings=result.out_of_bounds,
        display_trace=[{"t": tp.t, "x": tp.x, "y": tp.y, "pen": tp.pen_down} for tp in dport.trace],
        gcode=code,
    )
