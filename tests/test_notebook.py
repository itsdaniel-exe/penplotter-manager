"""Tests for writing into a ruled notebook in more than one pen.

The thing that has to hold is alignment: the passes describe the same lines of
the same pages, and if they slide apart the second pen writes its headings
against the wrong body text - a whole notebook page ruined, in ink.

    .venv\\Scripts\\python -m tests.test_notebook
"""

from __future__ import annotations

from ._harness import check, report, run

from plotter.config import TextStyle
from plotter.handwriting import HandStyle
from plotter.notebook import (
    NotebookConfig,
    PenPass,
    align_passes,
    build_notebook_pages,
    fit_font_size,
    wrap_passes,
)
from plotter.fonts import load_font


def _passes(black: list[str], blue: list[str]) -> list[PenPass]:
    return [PenPass("Black pen", "#1b1b1b", black), PenPass("Blue pen", "#1f3f8f", blue)]


def test_passes_stay_aligned():
    """Two documents describing the same pages: one line, one pen."""
    print("pen passes share one line grid")
    black = ["AIM:", "", "PROCEDURE:"]
    blue = ["", "To do the thing.", ""]

    aligned = align_passes(_passes(black, blue + ["extra tail line"]))
    check("a shorter pass is padded, not re-flowed",
          len(aligned[0].lines) == len(aligned[1].lines) == 4, str([len(p.lines) for p in aligned]))
    check("padding goes at the end", aligned[0].lines[:3] == black, str(aligned[0].lines))

    pages, _ = build_notebook_pages(_passes(black, blue), NotebookConfig(lines_per_page=24),
                                    TextStyle(), None)
    page = pages[0]
    check("each pen writes only its own lines",
          [p.written_lines for p in page.passes] == [2, 1], str([p.written_lines for p in page.passes]))


def test_baselines_land_on_the_ruled_lines():
    """A notebook's lines are already printed; the writing has to sit on them."""
    print("baselines land on the rules")
    nb = NotebookConfig(first_line_mm=30, line_spacing_mm=8, lines_per_page=4, margin_left_mm=20)
    # one word per line so each line's baseline is easy to identify
    pages, _ = build_notebook_pages(_passes(["one", "two", "three", "four"], []), nb,
                                    TextStyle(font_size_mm=4), None, auto_fit=False)
    strokes = pages[0].passes[0].strokes

    # the lowest point of each line's glyphs sits on (or just under) its rule
    rules = [30.0, 38.0, 46.0, 54.0]
    bottoms = sorted({round(max(y for _, y in s), 1) for s in strokes})
    for rule in rules:
        near = any(abs(b - rule) <= 0.6 for b in bottoms)
        check(f"a line sits on the {rule:.0f}mm rule", near, str(bottoms))


def test_pagination_is_by_line_count():
    """Not by measured height: the notebook decides how many lines fit."""
    print("pages hold exactly the notebook's line count")
    lines = [f"line {i}" for i in range(50)]
    nb = NotebookConfig(lines_per_page=20)
    pages, _ = build_notebook_pages(_passes(lines, []), nb, TextStyle(), None)

    check("50 lines over 20 per page is 3 pages", len(pages) == 3, str(len(pages)))
    check("the first page reports its source lines",
          (pages[0].first_line_no, pages[0].last_line_no) == (1, 20),
          f"{pages[0].first_line_no}-{pages[0].last_line_no}")
    check("the last page holds the remainder",
          pages[2].passes[0].written_lines == 10, str(pages[2].passes[0].written_lines))

    # blank lines hold their slot - they are the spacing the document intended
    spaced, _ = build_notebook_pages(_passes(["a", "", "", "b"], []), NotebookConfig(lines_per_page=4),
                                     TextStyle(), None)
    ys = sorted({round(max(y for _, y in s)) for s in spaced[0].passes[0].strokes})
    check("a blank line still uses up a rule", ys[1] - ys[0] >= 24, str(ys))


def test_wrapping_keeps_every_pass_in_step():
    """A line that has to be split pushes the other pens down too, or the
    passes stop describing the same page."""
    print("wrapping keeps the passes aligned")
    font = load_font("HersheySansMed")
    style = TextStyle(font_size_mm=5)
    scale = style.font_size_mm / font.units_per_em

    long_line = "this single line is far too wide for the narrow page it is being written onto"
    passes = align_passes(_passes(["short", long_line, "after"], ["one", "", "three"]))
    wrapped, count = wrap_passes(passes, font, style, scale, width_mm=40)

    check("the over-long line was split", count == 1, str(count))
    check("both passes grew by the same amount",
          len(wrapped[0].lines) == len(wrapped[1].lines), str([len(p.lines) for p in wrapped]))
    check("the line after it is still opposite its partner",
          wrapped[0].lines[-1] == "after" and wrapped[1].lines[-1] == "three",
          str((wrapped[0].lines[-1], wrapped[1].lines[-1])))
    check("every wrapped piece now fits the width",
          all(font.text_width(line) * scale <= 40 for line in wrapped[0].lines), "")


def test_size_is_chosen_to_fit_the_ruling_and_the_width():
    print("text is sized to the notebook, not the other way round")
    nb = NotebookConfig(line_spacing_mm=8, margin_left_mm=25, margin_right_mm=12, page_width_mm=210)
    roomy = fit_font_size(_passes(["short line"], []), nb, TextStyle())
    check("a short line is capped by the rule spacing",
          roomy <= nb.line_spacing_mm, f"{roomy}mm for {nb.line_spacing_mm}mm rules")

    font = load_font("HersheySansMed")
    # A long line that can still be made to fit: the size drops until it does.
    wide = ("a considerably longer line of body text that has to be shrunk a good deal "
            "before it will fit across the width of the page")
    tight = fit_font_size(_passes([wide], []), nb, TextStyle())
    check("a wide line forces a smaller size", tight < roomy, f"{tight} vs {roomy}")
    check("and that size really does fit the page width",
          font.text_width(wide) * (tight / font.units_per_em) <= nb.writable_width_mm + 0.1,
          str(font.text_width(wide) * (tight / font.units_per_em)))

    # One unbreakable 200-character word cannot fit at any legible size. Shrinking
    # to a floor and splitting it beats writing something the pen can't resolve.
    hopeless = fit_font_size(_passes(["x" * 200], []), nb, TextStyle())
    check("an impossible line stops at the legible floor", hopeless == 2.2, str(hopeless))
    pages, warnings = build_notebook_pages(_passes(["x" * 200], []), nb, TextStyle(), None)
    check("and the operator is told it had to be split",
          any("split" in w for w in warnings), str(warnings))


def test_impossible_notebooks_are_refused():
    print("notebook settings that cannot work")
    for kwargs, wanted in [
        ({"margin_left_mm": 120, "margin_right_mm": 120}, "width"),
        ({"line_spacing_mm": 0}, "spacing"),
        ({"lines_per_page": 0}, "line"),
        ({"first_line_mm": 280, "lines_per_page": 24}, "off the bottom"),
    ]:
        try:
            NotebookConfig(**kwargs).validate()
            check(f"{wanted} is rejected", False, "no error raised")
        except ValueError as e:
            check(f"{wanted} is rejected", wanted in str(e).lower(), str(e))


def test_a_page_is_reproducible():
    """The second pen is written minutes after the first, and Preview has to
    match both. Same inputs must give the same page every time."""
    print("pages are reproducible across renders")
    args = (_passes(["AIM:", "", "RESULT:"], ["", "to test it", ""]),
            NotebookConfig(lines_per_page=12), TextStyle(), HandStyle(seed=7))
    first, _ = build_notebook_pages(*args)
    second, _ = build_notebook_pages(*args)
    check("same seed, same strokes",
          [p.strokes for p in first[0].passes] == [p.strokes for p in second[0].passes])

    other, _ = build_notebook_pages(_passes(["AIM:", "", "RESULT:"], ["", "to test it", ""]),
                                    NotebookConfig(lines_per_page=12), TextStyle(),
                                    HandStyle(seed=8))
    check("a different seed gives a different hand",
          [p.strokes for p in first[0].passes] != [p.strokes for p in other[0].passes])


def main() -> int:
    run([test_passes_stay_aligned,
         test_baselines_land_on_the_ruled_lines,
         test_pagination_is_by_line_count,
         test_wrapping_keeps_every_pass_in_step,
         test_size_is_chosen_to_fit_the_ruling_and_the_width,
         test_impossible_notebooks_are_refused,
         test_a_page_is_reproducible])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
