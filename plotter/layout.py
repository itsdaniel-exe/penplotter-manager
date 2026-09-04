"""Word-wrap and paginate plain text into pages of placed pen strokes,
using a loaded stroke font. All coordinates are page-space mm with the
origin at the page's top-left corner, x right, y down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import PageConfig, TextStyle
from .fonts import Font, load_font

Point = tuple[float, float]
Stroke = list[Point]


@dataclass
class PlacedText:
    """One page's worth of pen strokes, in page-space mm."""
    strokes: list[Stroke]
    page_index: int


@dataclass
class _Word:
    text: str
    width: float  # font units


def _split_paragraphs(text: str) -> list[str]:
    # Normalize line endings, treat blank lines as paragraph breaks.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of blank lines but keep single explicit newlines as
    # forced breaks within a paragraph.
    return text.split("\n")


def _wrap_line(line: str, font: Font, max_width_units: float) -> list[str]:
    if not line.strip():
        return [""]
    words = line.split(" ")
    wrapped: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if font.text_width(candidate) <= max_width_units or not current:
            current = candidate
        else:
            wrapped.append(current)
            current = word
    if current:
        wrapped.append(current)
    return wrapped or [""]


def layout_text(text: str, page: PageConfig, style: TextStyle) -> list[PlacedText]:
    """Lay out `text` onto one or more pages, wrapping and paginating to
    fit `page`'s content box at `style`'s font/size/spacing."""
    font = load_font(style.font)
    scale = style.font_size_mm / font.units_per_em
    max_width_units = page.content_width_mm / scale

    lines: list[str] = []
    for para in _split_paragraphs(text):
        lines.extend(_wrap_line(para, font, max_width_units))

    lines_per_page = max(1, int(page.content_height_mm // style.line_spacing_mm))
    first_baseline_offset = font.ascent * scale

    pages: list[PlacedText] = []
    for start in range(0, len(lines), lines_per_page):
        chunk = lines[start : start + lines_per_page]
        strokes: list[Stroke] = []
        for row, line in enumerate(chunk):
            baseline_y = page.margin_top_mm + first_baseline_offset + row * style.line_spacing_mm
            line_width_units = font.text_width(line)
            line_width_mm = line_width_units * scale + max(0, len(line) - 1) * style.letter_spacing_mm

            if style.align == "center":
                x_cursor = page.margin_left_mm + (page.content_width_mm - line_width_mm) / 2
            elif style.align == "right":
                x_cursor = page.margin_left_mm + (page.content_width_mm - line_width_mm)
            else:
                x_cursor = page.margin_left_mm

            for ch in line:
                glyph = font.glyph(ch)
                if glyph is None:
                    x_cursor += font.default_horiz_adv_x * scale + style.letter_spacing_mm
                    continue
                for gstroke in glyph.strokes:
                    strokes.append(
                        [
                            (x_cursor + gx * scale, baseline_y - gy * scale)
                            for gx, gy in gstroke
                        ]
                    )
                x_cursor += glyph.horiz_adv_x * scale + style.letter_spacing_mm

        pages.append(PlacedText(strokes=strokes, page_index=len(pages)))

    return pages or [PlacedText(strokes=[], page_index=0)]
