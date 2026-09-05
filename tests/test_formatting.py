"""Tests for auto-fit and handwriting realism.

The property that matters most here is **reproducibility**: Preview, Simulate
and the real Run each lay the page out independently, so if the handwriting
jitter were re-rolled per render, the preview would be showing a page the pen
never draws. Everything else is about staying inside the paper.
"""

from __future__ import annotations

from ._harness import check, report, run

from plotter.autofit import LINE_SPACING_RATIO, MAX_FONT_MM, fit_font_size
from plotter.config import PageConfig, TextStyle
from plotter.handwriting import HandStyle
from plotter.layout import layout_text

PAGE = PageConfig(width_mm=195, height_mm=295, margin_top_mm=15, margin_bottom_mm=15,
                  margin_left_mm=12, margin_right_mm=12)
STYLE = TextStyle(font="HersheyScriptMed", font_size_mm=4.5, line_spacing_mm=7.4)

SHORT = "Dear Alex,\n\nThank you for your order.\n\nWarm regards,\nThe Team"
LONG = " ".join(["This is a formal letter body sentence for testing."] * 60)


def _bbox(strokes):
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    return min(xs), min(ys), max(xs), max(ys)


def test_autofit():
    print("auto-fit")
    short_size, short_spacing = fit_font_size(SHORT, PAGE, STYLE)
    long_size, _ = fit_font_size(LONG, PAGE, STYLE)

    check("a short note is not blown up past the formal maximum",
          short_size <= MAX_FONT_MM, f"got {short_size}mm")
    check("more text gets smaller text", long_size < short_size,
          f"short {short_size}mm vs long {long_size}mm")
    check("line spacing follows the font size",
          abs(short_spacing - short_size * LINE_SPACING_RATIO) < 0.02)

    # The point of auto-fit: whatever the length, it lands on one page.
    for label, text in (("short", SHORT), ("long", LONG)):
        size, spacing = fit_font_size(text, PAGE, STYLE)
        fitted = TextStyle(font=STYLE.font, font_size_mm=size, line_spacing_mm=spacing)
        pages = layout_text(text, PAGE, fitted)
        check(f"{label} text fits on one page at the fitted size",
              len(pages) == 1, f"got {len(pages)} pages")

    # A wall of text can't fit at a legible size; more pages is the honest
    # outcome, rather than shrinking to something the pen can't draw.
    huge = " ".join(["word"] * 20000)
    size, _ = fit_font_size(huge, PAGE, STYLE)
    check("an impossible amount of text stops at the legible minimum",
          size >= 2.0, f"got {size}mm")


def test_handwriting_is_reproducible():
    print("handwriting reproducibility")
    a = layout_text(SHORT, PAGE, STYLE, HandStyle(seed=42))[0].strokes
    b = layout_text(SHORT, PAGE, STYLE, HandStyle(seed=42))[0].strokes
    c = layout_text(SHORT, PAGE, STYLE, HandStyle(seed=43))[0].strokes

    check("same seed lays out identically, so Run matches Preview", a == b)
    check("a different seed gives a different hand", a != c)

    plain = layout_text(SHORT, PAGE, STYLE, None)[0].strokes
    check("realism changes the output", a != plain)
    check("realism adds no strokes - the pen draws the same letters",
          len(a) == len(plain), f"{len(a)} vs {len(plain)}")

    off = layout_text(SHORT, PAGE, STYLE, HandStyle(enabled=False))[0].strokes
    check("disabled realism is identical to no realism at all", off == plain)
    zero = layout_text(SHORT, PAGE, STYLE, HandStyle(amount=0))[0].strokes
    check("zero amount is identical to no realism at all", zero == plain)


def test_handwriting_varies_letters():
    """A glyph may be drawn with several strokes, so compare whole letters -
    not stroke 0 against stroke 1, which can both belong to the same letter."""
    print("handwriting variation")
    big = TextStyle(font="HersheyScriptMed", font_size_mm=20, line_spacing_mm=30)
    letter = "a"

    per_letter = len(layout_text(letter, PAGE, big, None)[0].strokes)
    check("test setup: the sample letter draws at least one stroke", per_letter >= 1,
          f"{per_letter} strokes")

    def letter_shape(strokes, index):
        """One letter's strokes, moved to a common origin so only shape and
        relative placement are compared, not where it sits on the line."""
        group = strokes[index * per_letter : (index + 1) * per_letter]
        ox, oy = group[0][0]
        return [[(round(x - ox, 4), round(y - oy, 4)) for x, y in s] for s in group]

    hand = layout_text(letter * 3, PAGE, big, HandStyle(seed=3))[0].strokes
    plain = layout_text(letter * 3, PAGE, big, None)[0].strokes

    check("without realism the same letter is drawn identically every time",
          letter_shape(plain, 0) == letter_shape(plain, 1) == letter_shape(plain, 2))
    check("with realism the same letter differs between occurrences - the whole point",
          letter_shape(hand, 0) != letter_shape(hand, 1)
          and letter_shape(hand, 1) != letter_shape(hand, 2))


def test_handwriting_stays_on_the_paper():
    """Jitter must not push ink off the sheet. Margins give it room, but a
    bug in the magnitudes would show up here rather than on wasted paper."""
    print("handwriting stays inside the page")
    text = "Dear Mr Whitfield, thank you for your letter of the fourteenth. " * 12

    for amount in (1.0, 2.0):
        strokes = layout_text(text, PAGE, STYLE, HandStyle(seed=5, amount=amount))[0].strokes
        x0, y0, x1, y1 = _bbox(strokes)
        check(f"amount {amount}: nothing runs off the sheet",
              x0 >= 0 and y0 >= 0 and x1 <= PAGE.width_mm and y1 <= PAGE.height_mm,
              f"bbox {x0:.1f},{y0:.1f} -> {x1:.1f},{y1:.1f}")

        plain = layout_text(text, PAGE, STYLE, None)[0].strokes
        px0, py0, px1, py1 = _bbox(plain)
        drift = max(abs(x0 - px0), abs(y0 - py0), abs(x1 - px1), abs(y1 - py1))
        # Handwriting should wander a little past a ruled margin - that is what
        # makes it look human - but a couple of mm, not centimetres.
        check(f"amount {amount}: drift past the typeset extent stays small",
              drift < 3.0, f"drifted {drift:.2f}mm")


def main() -> int:
    run([test_autofit, test_handwriting_is_reproducible,
         test_handwriting_varies_letters, test_handwriting_stays_on_the_paper])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
