"""Render placed strokes to an SVG so a job can be eyeballed before it's
sent to the plotter - open the file in a browser or Inkscape."""

from __future__ import annotations

from .config import PageConfig
from .layout import PlacedText


def render_svg(page_layout: PlacedText, page: PageConfig) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page.width_mm}mm" height="{page.height_mm}mm" '
        f'viewBox="0 0 {page.width_mm} {page.height_mm}">',
        f'<rect x="0" y="0" width="{page.width_mm}" height="{page.height_mm}" fill="white" stroke="#ccc" stroke-width="0.3"/>',
        f'<rect x="{page.margin_left_mm}" y="{page.margin_top_mm}" '
        f'width="{page.content_width_mm}" height="{page.content_height_mm}" '
        f'fill="none" stroke="#eee" stroke-width="0.2" stroke-dasharray="2,2"/>',
    ]
    for stroke in page_layout.strokes:
        if len(stroke) < 2:
            continue
        d = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in stroke)
        parts.append(f'<path d="{d}" fill="none" stroke="black" stroke-width="0.3"/>')
    parts.append("</svg>")
    return "\n".join(parts)
