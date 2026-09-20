"""Write into a ruled notebook, in more than one pen.

Two things make this different from laying text out on a loose sheet:

**The lines already exist.** A notebook is pre-ruled, so the layout is not free
to choose its own line spacing - every baseline has to land on a printed rule,
and a page holds exactly as many lines as the notebook has rules. Pagination is
therefore by line count, not by measured height.

**A document can be written in several pens.** A lab record has its headings in
one colour and its body in another. Those are supplied as separate documents
that share one line grid: line 12 is blue in one file and blank in the other.
The machine writes one pen's lines for a page, waits for the pen to be changed,
writes the next pen's lines on the same page, then waits for the page to be
turned. Keeping the grid aligned is the whole job here - a line inserted in one
pass and not the others would put every following line on the wrong rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import TextStyle
from .fonts import Font, load_font
from .handwriting import Hand, HandStyle
from .layout import (
    Stroke,
    align_start_x,
    normalise_text,
    place_line,
    text_width_mm,
)

# A line of handwriting sits well inside its rule at roughly this fraction of
# the rule spacing; much larger and ascenders collide with the line above.
SIZE_TO_RULE_RATIO = 0.62
MIN_FONT_MM = 2.2


@dataclass
class NotebookConfig:
    """The physical notebook, measured once with a ruler.

    `first_line_mm` is from the top edge of the page to the first ruled line
    the pen should write on, and `lines_per_page` is how many rules are usable
    on one page - not necessarily how many are printed, since the last one or
    two are often too close to the binding edge to reach.
    """

    page_width_mm: float = 210.0
    page_height_mm: float = 297.0
    margin_left_mm: float = 25.0
    margin_right_mm: float = 12.0
    first_line_mm: float = 30.0
    line_spacing_mm: float = 8.0
    lines_per_page: int = 24

    @property
    def writable_width_mm(self) -> float:
        return self.page_width_mm - self.margin_left_mm - self.margin_right_mm

    def validate(self) -> None:
        if self.writable_width_mm <= 0:
            raise ValueError("The margins leave no width to write in on the notebook page.")
        if self.line_spacing_mm <= 0:
            raise ValueError("Ruled line spacing must be more than 0mm.")
        if self.lines_per_page < 1:
            raise ValueError("A notebook page needs at least one usable line.")
        last_line = self.first_line_mm + (self.lines_per_page - 1) * self.line_spacing_mm
        if last_line > self.page_height_mm:
            raise ValueError(
                f"{self.lines_per_page} lines at {self.line_spacing_mm}mm starting "
                f"{self.first_line_mm}mm down runs {last_line - self.page_height_mm:.0f}mm "
                "off the bottom of the page."
            )


@dataclass
class PenPass:
    """One pen's worth of a document: its lines, aligned to the shared grid."""

    name: str
    colour: str
    lines: list[str] = field(default_factory=list)


@dataclass
class PlacedPass:
    name: str
    colour: str
    strokes: list[Stroke]
    written_lines: int


@dataclass
class NotebookPage:
    index: int
    passes: list[PlacedPass]
    first_line_no: int          # 1-based line number in the source grid
    last_line_no: int

    @property
    def is_blank(self) -> bool:
        return all(p.written_lines == 0 for p in self.passes)


def align_passes(passes: list[PenPass]) -> list[PenPass]:
    """Pad every pass to the same number of lines.

    The passes describe the same pages, so a shorter one just has nothing to
    say at the end - not a different grid.
    """
    if not passes:
        return []
    height = max(len(p.lines) for p in passes)
    return [
        PenPass(p.name, p.colour, list(p.lines) + [""] * (height - len(p.lines)))
        for p in passes
    ]


def _wrap_line(line: str, font: Font, style: TextStyle, scale: float, width_mm: float) -> list[str]:
    """Break one line to the writable width, on spaces where possible."""
    if not line.strip() or text_width_mm(line, font, style, scale) <= width_mm:
        return [line]

    out: list[str] = []
    current = ""
    for word in line.split(" "):
        candidate = f"{current} {word}".strip() if current else word
        if text_width_mm(candidate, font, style, scale) <= width_mm:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        # a single word wider than the page still has to go somewhere
        while text_width_mm(word, font, style, scale) > width_mm and len(word) > 1:
            cut = len(word)
            while cut > 1 and text_width_mm(word[:cut], font, style, scale) > width_mm:
                cut -= 1
            out.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        out.append(current)
    return out or [""]


def wrap_passes(
    passes: list[PenPass], font: Font, style: TextStyle, scale: float, width_mm: float
) -> tuple[list[PenPass], int]:
    """Re-wrap any line too wide for the page, keeping every pass in step.

    When a line wraps into two, every *other* pass gains a blank line at the
    same place. Without that the passes would slide out of alignment and the
    second pen would write its headings against the wrong body text.
    """
    if not passes:
        return [], 0

    grid: list[list[str]] = [[] for _ in passes]
    wrapped_count = 0
    for row in range(len(passes[0].lines)):
        pieces = [_wrap_line(p.lines[row], font, style, scale, width_mm) for p in passes]
        tallest = max(len(piece) for piece in pieces)
        if tallest > 1:
            wrapped_count += 1
        for i, piece in enumerate(pieces):
            grid[i].extend(piece + [""] * (tallest - len(piece)))

    return [PenPass(p.name, p.colour, grid[i]) for i, p in enumerate(passes)], wrapped_count


def fit_font_size(
    passes: list[PenPass], notebook: NotebookConfig, style: TextStyle, max_font_mm: float | None = None
) -> float:
    """The largest size that sits inside the rules and still fits the widest line.

    Handwriting in a notebook is constrained twice over: by the rule spacing
    vertically and by the page width horizontally. Whichever binds first wins.
    """
    font = load_font(style.font)
    ceiling = max_font_mm or notebook.line_spacing_mm * SIZE_TO_RULE_RATIO
    size = ceiling
    while size >= MIN_FONT_MM:
        scale = size / font.units_per_em
        widest = max(
            (text_width_mm(line, font, style, scale) for p in passes for line in p.lines),
            default=0.0,
        )
        if widest <= notebook.writable_width_mm:
            return round(size, 2)
        size -= 0.1
    return MIN_FONT_MM


def build_notebook_pages(
    passes: list[PenPass],
    notebook: NotebookConfig,
    style: TextStyle,
    hand: HandStyle | None = None,
    auto_fit: bool = True,
) -> tuple[list[NotebookPage], list[str]]:
    """Lay the passes out onto notebook pages. Returns the pages and warnings."""
    notebook.validate()
    if not passes:
        return [], []

    font = load_font(style.font)
    warnings: list[str] = []

    if auto_fit:
        size_mm = fit_font_size(passes, notebook, style)
    else:
        size_mm = style.font_size_mm
    if size_mm <= 0:
        raise ValueError("Font size must be more than 0mm.")
    if size_mm > notebook.line_spacing_mm:
        warnings.append(
            f"{size_mm}mm text is taller than the {notebook.line_spacing_mm}mm ruling - "
            "lines will run into each other."
        )

    scale = size_mm / font.units_per_em
    passes = align_passes(passes)
    passes, wrapped = wrap_passes(passes, font, style, scale, notebook.writable_width_mm)
    if wrapped:
        warnings.append(
            f"{wrapped} line(s) were too wide for the page and had to be split, "
            "so the text runs one or more lines longer than the document."
        )

    missing = sorted({
        ch
        for p in passes
        for line in p.lines
        for ch in font.missing(normalise_text(line))
    })
    if missing:
        warnings.append(
            f"{len(missing)} character(s) this font cannot draw were left blank: "
            + " ".join(missing[:8])
        )

    line_style = TextStyle(
        font=style.font,
        font_size_mm=size_mm,
        line_spacing_mm=notebook.line_spacing_mm,
        letter_spacing_mm=style.letter_spacing_mm,
        align=style.align,
    )

    total_lines = len(passes[0].lines)
    per_page = notebook.lines_per_page
    pages: list[NotebookPage] = []

    for page_no, start in enumerate(range(0, total_lines, per_page)):
        placed: list[PlacedPass] = []
        for pass_no, pen in enumerate(passes):
            chunk = pen.lines[start : start + per_page]
            # A fresh hand per page and pen, seeded from the base seed, so each
            # pass is reproducible on its own - Preview, Simulate and the real
            # run all have to draw the identical page, and the second pen is
            # written minutes after the first.
            h = None
            if hand and hand.enabled and hand.amount > 0:
                seeded = HandStyle(enabled=True, amount=hand.amount,
                                   seed=hand.seed + page_no * 97 + pass_no * 7919)
                h = Hand(seeded, size_mm)

            strokes: list[Stroke] = []
            written = 0
            for row, line in enumerate(chunk):
                if not line.strip():
                    continue
                written += 1
                baseline_y = notebook.first_line_mm + row * notebook.line_spacing_mm
                x_cursor = align_start_x(line, font, line_style, scale,
                                         notebook.margin_left_mm, notebook.writable_width_mm)
                drift = h.line_baseline(notebook.writable_width_mm) if h else None
                if h:
                    x_cursor += h.line_start_offset()
                strokes.extend(
                    place_line(line, font, line_style, scale, x_cursor, baseline_y, h, drift,
                               origin_x_mm=notebook.margin_left_mm)
                )
            placed.append(PlacedPass(pen.name, pen.colour, strokes, written))

        pages.append(NotebookPage(
            index=page_no,
            passes=placed,
            first_line_no=start + 1,
            last_line_no=min(start + per_page, total_lines),
        ))

    return pages, warnings
