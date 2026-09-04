"""Command-line entry point.

Typical one-shot job:
    plot run --text "Dear Alex, ..." --page a4 --font HersheyScriptMed --port COM3

Typical order-from-document job:
    plot run --file order_letter.pdf --page custom-card --port COM3

Always safe to preview first:
    plot preview --file order_letter.pdf --page custom-card
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

import json
import re

from . import extract, gcode as gcode_mod, jobs as jobs_mod, svgin
from .config import MachineConfig, PageConfig, TextStyle, PAGE_SIZES
from .fonts import list_fonts
from .layout import layout_text
from .preview import render_svg
from .simulator import FakeGrblPort
from .stream import GrblStreamer, list_ports

JOBS_DIR = Path(__file__).resolve().parent.parent / "jobs"

_BOUNDS_RE = re.compile(r"^([XY])=(-?[\d.]+) outside 0\.\.([\d.]+)mm bed (\w+)$")


def _summarize_bounds(warnings: list[str]) -> list[str]:
    """Collapse thousands of per-point out-of-bounds warnings into one
    line per axis: how many points, how far over the limit."""
    by_axis: dict[str, list[float]] = {}
    limit_by_axis: dict[str, tuple[float, str]] = {}
    for w in warnings:
        m = _BOUNDS_RE.match(w)
        if not m:
            continue
        axis, value, limit, kind = m.groups()
        by_axis.setdefault(axis, []).append(float(value))
        limit_by_axis[axis] = (float(limit), kind)

    out = []
    for axis, values in by_axis.items():
        limit, kind = limit_by_axis[axis]
        worst = max(values, key=lambda v: max(v - limit, -v))
        over = worst - limit if worst > limit else -worst
        out.append(
            f"{axis} exceeded 0..{limit:.0f}mm bed {kind} on {len(values)} point(s), "
            f"worst {worst:.1f}mm ({over:.1f}mm over)"
        )
    return out


def _common_page_options(f):
    f = click.option("--text", "text", default=None, help="Raw text to write.")(f)
    f = click.option("--file", "doc_file", type=click.Path(exists=True), default=None,
                      help="Document to extract text from (.txt/.pdf/.docx).")(f)
    f = click.option("--svg", "svg_file", type=click.Path(exists=True), default=None,
                      help="Existing SVG artwork to plot as-is (fit to page).")(f)
    f = click.option("--page", "page_size", default="a4",
                      type=click.Choice(sorted(PAGE_SIZES)), help="Page size.")(f)
    f = click.option("--width-mm", type=float, default=None, help="Override page width (mm).")(f)
    f = click.option("--height-mm", type=float, default=None, help="Override page height (mm).")(f)
    f = click.option("--landscape", is_flag=True, default=False)(f)
    f = click.option("--margin-mm", type=float, default=None, help="Set all four margins at once.")(f)
    f = click.option("--font", "font_name", default="HersheySansMed",
                      help="Font name (see `plot fonts`).")(f)
    f = click.option("--font-size-mm", type=float, default=5.0)(f)
    f = click.option("--line-spacing-mm", type=float, default=8.0)(f)
    f = click.option("--align", type=click.Choice(["left", "center", "right"]), default="left")(f)
    return f


def _resolve_page(page_size, width_mm, height_mm, landscape, margin_mm) -> PageConfig:
    page = PageConfig.from_size(page_size, landscape=landscape)
    if width_mm:
        page.width_mm = width_mm
    if height_mm:
        page.height_mm = height_mm
    if margin_mm is not None:
        page.margin_top_mm = page.margin_bottom_mm = page.margin_left_mm = page.margin_right_mm = margin_mm
    return page


def _resolve_input_text(text, doc_file) -> str:
    if text:
        return text
    if doc_file:
        return extract.extract_text(doc_file)
    raise click.UsageError("Provide --text, --file, or --svg.")


def _build_pages(text, doc_file, svg_file, page: PageConfig, style: TextStyle):
    if not (text or doc_file or svg_file):
        raise click.UsageError("Provide --text, --file, or --svg.")
    return jobs_mod.build_pages(jobs_mod.JobInput(text=text, doc_path=doc_file, svg_path=svg_file), page, style)


@click.group()
def cli():
    """Local automation for the pen plotter: text/document/SVG -> formatted page -> gcode -> machine."""


@cli.command("fonts")
def fonts_cmd():
    """List available stroke fonts."""
    for name in list_fonts():
        click.echo(name)


@cli.command("ports")
def ports_cmd():
    """List available serial ports."""
    ports = list_ports()
    if not ports:
        click.echo("No serial ports found.")
    for p in ports:
        click.echo(p)


@cli.command("preview")
@_common_page_options
def preview_cmd(text, doc_file, svg_file, page_size, width_mm, height_mm, landscape,
                 margin_mm, font_name, font_size_mm, line_spacing_mm, align):
    """Render the formatted page(s) to SVG for a visual check, no machine needed."""
    page = _resolve_page(page_size, width_mm, height_mm, landscape, margin_mm)
    style = TextStyle(font=font_name, font_size_mm=font_size_mm, line_spacing_mm=line_spacing_mm, align=align)
    pages = _build_pages(text, doc_file, svg_file, page, style)

    JOBS_DIR.mkdir(exist_ok=True)
    for i, p in enumerate(pages, start=1):
        out = JOBS_DIR / f"preview_{i:02d}.svg"
        out.write_text(render_svg(p, page), encoding="utf-8")
        click.echo(f"wrote {out} ({len(p.strokes)} strokes)")


@cli.command("gcode")
@_common_page_options
@click.option("--out", "out_prefix", default=None, help="Output filename prefix (default: jobs/job).")
@click.option("--servo-up", type=int, default=10)
@click.option("--servo-down", type=int, default=50)
@click.option("--travel-feed", type=int, default=10000)
@click.option("--draw-feed", type=int, default=2500)
def gcode_cmd(text, doc_file, svg_file, page_size, width_mm, height_mm, landscape, margin_mm,
              font_name, font_size_mm, line_spacing_mm, align, out_prefix,
              servo_up, servo_down, travel_feed, draw_feed):
    """Generate .gcode file(s) without sending to the machine."""
    page = _resolve_page(page_size, width_mm, height_mm, landscape, margin_mm)
    style = TextStyle(font=font_name, font_size_mm=font_size_mm, line_spacing_mm=line_spacing_mm, align=align)
    pages = _build_pages(text, doc_file, svg_file, page, style)
    machine = MachineConfig(servo_up=servo_up, servo_down=servo_down,
                             travel_feed=travel_feed, draw_feed=draw_feed)

    JOBS_DIR.mkdir(exist_ok=True)
    prefix = out_prefix or str(JOBS_DIR / "job")
    paths = []
    for i, p in enumerate(pages, start=1):
        code = gcode_mod.strokes_to_gcode(p, page.height_mm, machine, page.width_mm)
        out = Path(f"{prefix}_{i:02d}.gcode")
        out.write_text(code, encoding="utf-8")
        paths.append(out)
        click.echo(f"wrote {out}")
    return paths


@cli.command("simulate")
@_common_page_options
@click.option("--servo-up", type=int, default=10)
@click.option("--servo-down", type=int, default=50)
@click.option("--travel-feed", type=int, default=10000)
@click.option("--draw-feed", type=int, default=2500)
@click.option("--bed-width-mm", type=float, default=None, help="If known, flags moves outside this width.")
@click.option("--bed-height-mm", type=float, default=None, help="If known, flags moves outside this height.")
def simulate_cmd(text, doc_file, svg_file, page_size, width_mm, height_mm, landscape, margin_mm,
                  font_name, font_size_mm, line_spacing_mm, align,
                  servo_up, servo_down, travel_feed, draw_feed, bed_width_mm, bed_height_mm):
    """Run the job through a simulated GRBL board - no hardware needed.

    Exercises the real streaming code (connect handshake, line-by-line
    ack, progress) against a fake controller, and reports estimated plot
    time, travel/draw distance, and any moves that would exceed a given
    bed size. Writes jobs/sim_trace.json for the visual playback.
    """
    page = _resolve_page(page_size, width_mm, height_mm, landscape, margin_mm)
    style = TextStyle(font=font_name, font_size_mm=font_size_mm, line_spacing_mm=line_spacing_mm, align=align)
    pages = _build_pages(text, doc_file, svg_file, page, style)
    machine = MachineConfig(servo_up=servo_up, servo_down=servo_down,
                             travel_feed=travel_feed, draw_feed=draw_feed)

    JOBS_DIR.mkdir(exist_ok=True)
    all_traces = []
    grand_total_time = 0.0
    grand_travel = 0.0
    grand_draw = 0.0
    all_warnings = []

    for i, p in enumerate(pages, start=1):
        result = jobs_mod.simulate_page(p, page, machine, bed_width_mm, bed_height_mm)
        click.echo(f"page {i}: {result.lines} lines streamed, ok. "
                   f"~{result.total_time_s:.1f}s plot time, "
                   f"{result.total_draw_mm:.0f}mm drawn, {result.total_travel_mm:.0f}mm travel.")
        if result.bounds_warnings:
            axis_ranges = _summarize_bounds(result.bounds_warnings)
            for msg in axis_ranges:
                click.secho(f"  [!] {msg}", fg="yellow")
            all_warnings.extend(result.bounds_warnings)

        grand_total_time += result.total_time_s
        grand_travel += result.total_travel_mm
        grand_draw += result.total_draw_mm
        all_traces.append(result.display_trace)

    trace_path = JOBS_DIR / "sim_trace.json"
    trace_path.write_text(json.dumps({
        "page_width_mm": page.width_mm,
        "page_height_mm": page.height_mm,
        "pages": all_traces,
    }), encoding="utf-8")

    click.echo("")
    click.echo(f"Total: ~{grand_total_time/60:.1f} min plot time across {len(pages)} page(s), "
               f"{grand_draw/1000:.1f}m drawn, {grand_travel/1000:.1f}m travel moves.")
    if all_warnings:
        click.secho(f"{len(all_warnings)} out-of-bounds warnings - check --bed-width-mm/--bed-height-mm "
                    f"against your machine's real travel.", fg="yellow")
    click.echo(f"Trace written to {trace_path}")


@cli.command("send")
@click.argument("gcode_file", type=click.Path(exists=True))
@click.option("--port", required=True)
@click.option("--baud", type=int, default=115200)
def send_cmd(gcode_file, port, baud):
    """Stream an existing .gcode file to the plotter over serial."""
    code = Path(gcode_file).read_text(encoding="utf-8")
    _stream_gcode(code, port, baud)


@cli.command("run")
@_common_page_options
@click.option("--port", required=True, help="Serial port, e.g. COM3 (see `plot ports`).")
@click.option("--baud", type=int, default=115200)
@click.option("--servo-up", type=int, default=10)
@click.option("--servo-down", type=int, default=50)
@click.option("--travel-feed", type=int, default=10000)
@click.option("--draw-feed", type=int, default=2500)
@click.option("--dry-run", is_flag=True, help="Generate + preview only, don't touch the serial port.")
def run_cmd(text, doc_file, svg_file, page_size, width_mm, height_mm, landscape, margin_mm,
            font_name, font_size_mm, line_spacing_mm, align, port, baud,
            servo_up, servo_down, travel_feed, draw_feed, dry_run):
    """One-shot: format the page(s) and write them, page by page, on the plotter."""
    page = _resolve_page(page_size, width_mm, height_mm, landscape, margin_mm)
    style = TextStyle(font=font_name, font_size_mm=font_size_mm, line_spacing_mm=line_spacing_mm, align=align)
    pages = _build_pages(text, doc_file, svg_file, page, style)
    machine = MachineConfig(port=port, baud=baud, servo_up=servo_up, servo_down=servo_down,
                             travel_feed=travel_feed, draw_feed=draw_feed)

    JOBS_DIR.mkdir(exist_ok=True)
    click.echo(f"{len(pages)} page(s) to plot.")

    if dry_run:
        for i, p in enumerate(pages, start=1):
            out = JOBS_DIR / f"dryrun_{i:02d}.svg"
            out.write_text(render_svg(p, page), encoding="utf-8")
            click.echo(f"[dry-run] wrote {out} ({len(p.strokes)} strokes)")
        return

    streamer = GrblStreamer(port, baud)
    click.echo(f"Connecting to {port} @ {baud}...")
    banner = streamer.connect()
    click.echo(banner or "(connected)")

    try:
        for i, p in enumerate(pages, start=1):
            code = gcode_mod.strokes_to_gcode(p, page.height_mm, machine, page.width_mm)
            gpath = JOBS_DIR / f"run_{i:02d}.gcode"
            gpath.write_text(code, encoding="utf-8")

            if len(pages) > 1:
                click.confirm(f"Load blank page {i}/{len(pages)} and press Enter to start", default=True, show_default=False)

            click.echo(f"Plotting page {i}/{len(pages)} ({len(p.strokes)} strokes)...")
            total = len(code.splitlines())
            with click.progressbar(length=total, label=f"page {i}") as bar:
                last = 0

                def on_progress(prog):
                    nonlocal last
                    bar.update(prog.line_no - last)
                    last = prog.line_no

                streamer.stream(code, on_progress=on_progress)
            click.echo(f"Page {i} done.")
    finally:
        streamer.close()


def _stream_gcode(code: str, port: str, baud: int):
    streamer = GrblStreamer(port, baud)
    click.echo(f"Connecting to {port} @ {baud}...")
    banner = streamer.connect()
    click.echo(banner or "(connected)")
    total = len(code.splitlines())
    try:
        with click.progressbar(length=total, label="plotting") as bar:
            last = 0

            def on_progress(prog):
                nonlocal last
                bar.update(prog.line_no - last)
                last = prog.line_no

            streamer.stream(code, on_progress=on_progress)
    finally:
        streamer.close()


def main():
    cli()


if __name__ == "__main__":
    main()
