"""Tests for what comes in: typed text that Word has "helpfully" reformatted,
words too long to fit a line, and hand-made SVGs.

    .venv\\Scripts\\python -m tests.test_inputs
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from ._harness import check, report, run

from plotter.config import PageConfig, TextStyle
from plotter.fonts import load_font
from plotter.layout import layout_text, normalise_text, wrapped_lines
from plotter.svgin import load_svg_strokes, parse_path_d


def test_word_punctuation_is_drawable():
    """A stroke font has no curly quotes, and the font falls back to the space
    glyph - so text pasted from Word plotted "don t" with nothing said."""
    print("typographic punctuation")
    font = load_font("HersheySansMed")

    raw = "don’t “quote” me — see p.4…"
    check("the font really cannot draw these", bool(font.missing(raw)), str(font.missing(raw)))

    fixed = normalise_text(raw)
    check("apostrophe survives as an apostrophe", "don't" in fixed, fixed)
    check("quotes become straight quotes", '"quote"' in fixed, fixed)
    check("em dash becomes a hyphen", " - " in fixed, fixed)
    check("ellipsis becomes three dots", fixed.endswith("..."), fixed)
    check("nothing is left that the pen cannot draw", font.missing(fixed) == [], str(font.missing(fixed)))


def test_unsupported_characters_are_reported():
    """What can't be substituted still vanishes into the space glyph, so the
    page has to say so rather than letting it be found on the paper."""
    print("characters the font cannot draw")
    pages = layout_text("tick ✓ star ★", PageConfig(), TextStyle())
    warnings = pages[0].warnings
    check("a warning is attached to the page", bool(warnings), str(warnings))
    check("it names the characters", "✓" in warnings[0] and "★" in warnings[0], warnings[0])


def test_long_words_are_broken():
    """An unbreakable word used to be emitted whole and run off the sheet -
    and off the end of the rails, on a machine with no limit switches."""
    print("a word too long for the line")
    font = load_font("HersheySansMed")
    page = PageConfig(width_mm=100, height_mm=100, margin_left_mm=10, margin_right_mm=10)
    style = TextStyle(font_size_mm=5, line_spacing_mm=8)
    max_units = page.content_width_mm / (style.font_size_mm / font.units_per_em)

    lines = wrapped_lines("x" + "y" * 200, font, max_units)
    check("it is split across lines", len(lines) > 1, str(len(lines)))
    check("every line fits the content width",
          all(font.text_width(line) <= max_units for line in lines))

    pages = layout_text("https://example.com/" + "a" * 120, page, style)
    xs = [x for p in pages for s in p.strokes for x, _ in s]
    right_margin = page.width_mm - page.margin_right_mm
    check("no ink past the right margin", max(xs) <= right_margin + 0.5,
          f"max x {max(xs):.1f} vs margin {right_margin:.1f}")


def test_bad_sizes_say_which_field_is_wrong():
    print("zero-sized text and impossible margins")
    for kwargs, wanted in [
        ({"font_size_mm": 0}, "Font size"),
        ({"line_spacing_mm": 0}, "Line spacing"),
    ]:
        try:
            layout_text("hi", PageConfig(), TextStyle(**kwargs))
            check(f"{wanted} of 0 is rejected", False, "no error raised")
        except ValueError as e:
            check(f"{wanted} of 0 is rejected", wanted in str(e), str(e))

    try:
        layout_text("hi", PageConfig(width_mm=50, margin_left_mm=30, margin_right_mm=30), TextStyle())
        check("margins wider than the page are rejected", False, "no error raised")
    except ValueError as e:
        check("margins wider than the page are rejected", "margins" in str(e).lower(), str(e))


def test_svg_paths():
    """Arcs, closed subpaths and transforms all plotted wrongly - and silently,
    which on a pass-through SVG is the whole job ruined."""
    print("svg path parsing")

    subpaths = parse_path_d("M 0 0 L 10 0 Z M 20 0 L 30 0")
    check("a Z does not swallow the next subpath's moveto", len(subpaths) == 2, str(subpaths))
    check("the second subpath starts where it should",
          subpaths[1][0] == (20.0, 0.0), str(subpaths[1][0]))

    arc = parse_path_d("M 0 0 A 10 10 0 0 1 20 0")[0]
    check("an arc is flattened, not cut to a chord", len(arc) > 4, str(len(arc)))
    check("the arc ends at its endpoint",
          abs(arc[-1][0] - 20.0) < 0.01 and abs(arc[-1][1]) < 0.01, str(arc[-1]))
    check("and bulges by about its radius",
          9.0 < abs(min(p[1] for p in arc)) < 10.1, str(min(p[1] for p in arc)))


def test_svg_document():
    print("svg document handling")
    doc = """<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="0 0 100 100">
      <defs><path d="M 0 0 L 100 100"/></defs>
      <g transform="translate(10,20)"><path d="M 0 0 L 10 0"/></g>
      <circle cx="50" cy="50" r="5"/>
      <text x="10" y="90">label</text>
      <rect x="0" y="0" width="4" height="4" style="display:none"/>
    </svg>"""

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.svg"
        path.write_text(doc, encoding="utf-8")
        placed = load_svg_strokes(str(path), PageConfig(width_mm=100, height_mm=100))

    check("a percentage width no longer fails the whole upload", placed.strokes != [])
    check("<defs> and display:none geometry is not drawn", len(placed.strokes) == 2, str(len(placed.strokes)))
    check("<text> is reported rather than silently dropped",
          any("text" in w for w in placed.warnings), str(placed.warnings))
    check("the warning says how to fix it",
          any("Object to Path" in w for w in placed.warnings), str(placed.warnings))


def main() -> int:
    run([test_word_punctuation_is_drawable,
         test_unsupported_characters_are_reported,
         test_long_words_are_broken,
         test_bad_sizes_say_which_field_is_wrong,
         test_svg_paths,
         test_svg_document])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
