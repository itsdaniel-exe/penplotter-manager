"""Choose a font size and line spacing that fill the writing area.

Without this the operator picks a size, previews, finds the text runs onto a
second page or leaves half the sheet empty, and adjusts by hand. Given the
text and the page, there is only one sensible answer, so compute it.

The search is over font size; line spacing follows from it by a ratio, since
handwriting with the lines too close or too far apart stops looking natural
regardless of the letter size.
"""

from __future__ import annotations

from dataclasses import replace

from .config import PageConfig, TextStyle
from .fonts import load_font
from .layout import wrapped_lines

# Line spacing as a multiple of font size. Handwriting on ruled paper sits at
# roughly 1.6-1.8x the cap height; below ~1.4 ascenders and descenders collide.
LINE_SPACING_RATIO = 1.65

# A formal letter is not a poster. Even with a page to spare, letters larger
# than this stop reading as correspondence, so a short note is allowed to
# leave white space rather than being blown up to fill the sheet.
MAX_FONT_MM = 7.0
MIN_FONT_MM = 2.2   # below this a stroke font stops being legible on paper
STEP_MM = 0.1       # search resolution; finer than the pen can resolve anyway


def _pages_needed(text: str, page: PageConfig, style: TextStyle, size_mm: float) -> int:
    """How many pages `text` needs at `size_mm`."""
    font = load_font(style.font)
    scale = size_mm / font.units_per_em
    max_width_units = page.content_width_mm / scale
    lines = wrapped_lines(text, font, max_width_units)

    spacing = size_mm * LINE_SPACING_RATIO
    lines_per_page = int(page.content_height_mm // spacing)
    if lines_per_page < 1:
        return 10**6  # doesn't fit at all at this size
    return -(-len(lines) // lines_per_page)  # ceil


def fit_font_size(
    text: str,
    page: PageConfig,
    style: TextStyle,
    target_pages: int = 1,
    max_font_mm: float = MAX_FONT_MM,
    min_font_mm: float = MIN_FONT_MM,
) -> tuple[float, float]:
    """Largest (font_size_mm, line_spacing_mm) fitting `text` in `target_pages`.

    If the text cannot fit even at the minimum size, returns the minimum -
    the caller then simply gets more pages, which is the honest outcome.
    """
    if not text.strip():
        size = min(max_font_mm, 5.0)
        return size, size * LINE_SPACING_RATIO

    # Walk down from the largest allowed size. The relationship between size
    # and page count is monotonic but steppy (wrapping changes in jumps), so a
    # linear scan at STEP_MM is more predictable than a binary search and is
    # still only ~50 iterations.
    size = max_font_mm
    while size >= min_font_mm:
        if _pages_needed(text, page, style, size) <= target_pages:
            return round(size, 2), round(size * LINE_SPACING_RATIO, 2)
        size -= STEP_MM

    return round(min_font_mm, 2), round(min_font_mm * LINE_SPACING_RATIO, 2)


def autofit_style(
    text: str,
    page: PageConfig,
    style: TextStyle,
    target_pages: int = 1,
) -> TextStyle:
    """Return a copy of `style` with size and spacing chosen to fit the page."""
    size, spacing = fit_font_size(text, page, style, target_pages=target_pages)
    return replace(style, font_size_mm=size, line_spacing_mm=spacing)
