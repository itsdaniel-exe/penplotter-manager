"""Make machine-plotted text read as hand-written rather than typeset.

A stroke font plotted verbatim is unmistakably mechanical: every 'e' is
pixel-identical, every baseline is dead straight, every letter sits exactly
one advance-width from the last. Real handwriting varies on all three, so
this module reintroduces that variation - in small, *correlated* amounts, so
the result still reads as careful formal handwriting rather than a scrawl.

What varies, in rough order of how much it sells the effect:

1. Baseline drift - lines wander and tilt slightly instead of ruling straight.
2. Per-glyph rotation, scale and offset - the same letter differs each time.
3. Stroke bowing - a "straight" pen stroke bows slightly, as a hand's does.
4. Spacing - letter advances and word gaps vary a little.
5. Left margin - line starts don't stack perfectly.

Everything is driven by one seeded RNG consumed in a fixed order, so the same
text always produces the same page. That matters: Preview, Simulate and the
real Run each re-render independently, and a preview that didn't match what
the pen actually draws would be worse than no preview at all.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

Point = tuple[float, float]
Stroke = list[Point]

# Base magnitudes at amount = 1.0. Ones named *_em are fractions of the font's
# em size, so the look holds at any text size; *_mm ones are absolute page
# distances. Tuned to stay on the "neat formal hand" side - roughly double
# these and it starts to look careless.
GLYPH_ROTATE_DEG = 1.1      # per-letter tilt
GLYPH_SCALE = 0.022         # per-letter size variation
GLYPH_OFFSET_EM = 0.018     # per-letter nudge off the baseline/advance
ADVANCE_JITTER_EM = 0.014   # letter-to-letter spacing
WORD_GAP_JITTER_EM = 0.05   # word-to-word spacing
STROKE_BOW_EM = 0.009       # how much a "straight" stroke bows
BASELINE_DRIFT_MM = 0.30    # slow wander of the baseline across the line
BASELINE_SLOPE = 0.0035     # per-line tilt, as a gradient
LINE_START_JITTER_MM = 0.35 # left margin raggedness


@dataclass
class HandStyle:
    """User-facing controls. `amount` scales every magnitude above; `seed`
    makes a given page reproducible (and lets the user reshuffle for a
    different-looking hand from the same text)."""
    enabled: bool = True
    amount: float = 1.0
    seed: int = 7

    def scaled(self, base: float) -> float:
        return base * self.amount


class Hand:
    """Per-render randomness source. One instance per page render."""

    def __init__(self, style: HandStyle, em_mm: float):
        self.style = style
        self.em_mm = em_mm  # one em, in mm - converts *_em magnitudes to mm
        self.rng = random.Random(style.seed)

    # -- helpers ---------------------------------------------------------

    def _em(self, base: float) -> float:
        """An *_em magnitude, in mm at the current text size."""
        return self.style.scaled(base) * self.em_mm

    def _gauss(self, sigma: float) -> float:
        """Bounded normal - keeps a rare 3-sigma draw from producing a letter
        that visibly falls out of the line."""
        if sigma <= 0:
            return 0.0
        return max(-2.0 * sigma, min(2.0 * sigma, self.rng.gauss(0.0, sigma)))

    # -- line-level ------------------------------------------------------

    def line_baseline(self, width_mm: float):
        """Return f(x) -> vertical offset for one line's baseline.

        Two slow sine components plus a constant tilt. Slow on purpose: a
        baseline that wobbles per-letter reads as a shaky hand, whereas one
        that drifts over the width of the line reads as a natural one.
        """
        amp = self.style.scaled(BASELINE_DRIFT_MM)
        slope = self.style.scaled(BASELINE_SLOPE) * self.rng.uniform(-1.0, 1.0)
        if amp <= 0 and slope == 0:
            return lambda x: 0.0

        span = max(width_mm, 1.0)
        a1 = self._gauss(amp * 0.6)
        a2 = self._gauss(amp * 0.3)
        # 0.5-1.5 cycles across the line, so it never repeats tightly
        w1 = self.rng.uniform(0.5, 1.5) * math.pi / span
        w2 = self.rng.uniform(1.5, 3.0) * math.pi / span
        p1 = self.rng.uniform(0, 2 * math.pi)
        p2 = self.rng.uniform(0, 2 * math.pi)

        def offset(x: float) -> float:
            return a1 * math.sin(w1 * x + p1) + a2 * math.sin(w2 * x + p2) + slope * x

        return offset

    def line_start_offset(self) -> float:
        return self._gauss(self.style.scaled(LINE_START_JITTER_MM))

    # -- glyph-level -----------------------------------------------------

    def advance_jitter(self, is_space: bool) -> float:
        base = WORD_GAP_JITTER_EM if is_space else ADVANCE_JITTER_EM
        return self._gauss(self._em(base))

    def glyph_transform(self, strokes: list[Stroke], em_units: float, adv_units: float) -> list[Stroke]:
        """Apply rotation, scale, offset and stroke bowing to one glyph.

        Coordinates are font units with y up, origin on the baseline at the
        glyph's left edge - the same space `layout` places glyphs in.
        """
        if not strokes:
            return strokes

        rot = math.radians(self._gauss(self.style.scaled(GLYPH_ROTATE_DEG)))
        scale = 1.0 + self._gauss(self.style.scaled(GLYPH_SCALE))
        # offsets are in em fractions -> font units
        off_x = self._gauss(self.style.scaled(GLYPH_OFFSET_EM)) * em_units
        off_y = self._gauss(self.style.scaled(GLYPH_OFFSET_EM)) * em_units

        # Rotate about the glyph's middle, a little above the baseline, so a
        # tilt pivots the way a pen does rather than swinging the letter off
        # its line.
        cx = adv_units * 0.5
        cy = em_units * 0.25
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        bow_amp = self.style.scaled(STROKE_BOW_EM) * em_units

        out: list[Stroke] = []
        for stroke in strokes:
            n = len(stroke)
            # A smooth half-sine bow along the stroke: straight lines come out
            # very slightly curved, which is what a hand does. Per-point noise
            # would instead look like a tremor.
            bx = self._gauss(bow_amp) if n > 2 and bow_amp > 0 else 0.0
            by = self._gauss(bow_amp) if n > 2 and bow_amp > 0 else 0.0

            pts: Stroke = []
            for i, (x, y) in enumerate(stroke):
                if n > 1 and (bx or by):
                    t = i / (n - 1)
                    b = math.sin(math.pi * t)
                    x += bx * b
                    y += by * b
                dx, dy = x - cx, y - cy
                rx = dx * cos_r - dy * sin_r
                ry = dx * sin_r + dy * cos_r
                pts.append((cx + rx * scale + off_x, cy + ry * scale + off_y))
            out.append(pts)
        return out
