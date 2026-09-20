"""Parse SVG stroke fonts (Hershey / EMS fonts as shipped with the 4xiDraw
Hershey Text extension) into glyph outlines made of polylines.

Font files live in fonts/*.svg and use the standard SVG font format: a
<font> element with <font-face> metrics and one <glyph> per character,
each glyph's `d` attribute is stroke-only path data (no fill regions).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree

from .paths import fonts_dir

FONTS_DIR = fonts_dir()

_SVG_NS = "http://www.w3.org/2000/svg"


def _tag(name: str) -> str:
    return f"{{{_SVG_NS}}}{name}"


@dataclass
class Glyph:
    unicode: str
    horiz_adv_x: float
    strokes: list[list[tuple[float, float]]] = field(default_factory=list)


@dataclass
class Font:
    name: str
    units_per_em: float
    ascent: float
    descent: float
    default_horiz_adv_x: float
    glyphs: dict[str, Glyph]

    def glyph(self, ch: str) -> Glyph | None:
        return self.glyphs.get(ch) or self.glyphs.get(" ")

    def missing(self, text: str) -> list[str]:
        """Characters this font cannot draw, in order of first appearance.

        They fall back to the space glyph, so they vanish from the page
        silently - the caller is expected to tell the operator."""
        seen, out = set(), []
        for ch in text:
            if ch in seen or ch in self.glyphs or ch in ("\n", "\r"):
                continue
            seen.add(ch)
            out.append(ch)
        return out

    def advance(self, ch: str) -> float:
        g = self.glyphs.get(ch)
        return g.horiz_adv_x if g else self.default_horiz_adv_x

    def text_width(self, text: str) -> float:
        """Width of text in font units (unscaled)."""
        return sum(self.advance(ch) for ch in text)


def _flatten_cubic(p0, p1, p2, p3, segments=10):
    pts = []
    for i in range(1, segments + 1):
        t = i / segments
        mt = 1 - t
        x = (mt**3) * p0[0] + 3 * (mt**2) * t * p1[0] + 3 * mt * (t**2) * p2[0] + (t**3) * p3[0]
        y = (mt**3) * p0[1] + 3 * (mt**2) * t * p1[1] + 3 * mt * (t**2) * p2[1] + (t**3) * p3[1]
        pts.append((x, y))
    return pts


def _flatten_quadratic(p0, p1, p2, segments=8):
    pts = []
    for i in range(1, segments + 1):
        t = i / segments
        mt = 1 - t
        x = (mt**2) * p0[0] + 2 * mt * t * p1[0] + (t**2) * p2[0]
        y = (mt**2) * p0[1] + 2 * mt * t * p1[1] + (t**2) * p2[1]
        pts.append((x, y))
    return pts


_TOKEN_RE = re.compile(r"[MLCQZmlcqz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_path(d: str) -> list[list[tuple[float, float]]]:
    """Parse simple glyph path data (M/L/C/Q/Z, relative variants) into
    a list of polylines (strokes). Glyph paths never have Z-closed fills;
    a new M starts a new stroke."""
    if not d:
        return []
    tokens = _TOKEN_RE.findall(d)
    strokes: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    pos = (0.0, 0.0)
    start = (0.0, 0.0)
    i = 0
    cmd = None

    def read_nums(n):
        nonlocal i
        vals = [float(tokens[i + k]) for k in range(n)]
        i += n
        return vals

    while i < len(tokens):
        tok = tokens[i]
        if tok in "MLCQZmlcqz":
            cmd = tok
            i += 1
        if cmd in ("M", "m"):
            x, y = read_nums(2)
            if cmd == "m" and current:
                x += pos[0]
                y += pos[1]
            if current:
                strokes.append(current)
            pos = (x, y)
            start = pos
            current = [pos]
            cmd = "L" if cmd == "M" else "l"
        elif cmd in ("L", "l"):
            x, y = read_nums(2)
            if cmd == "l":
                x += pos[0]
                y += pos[1]
            pos = (x, y)
            current.append(pos)
        elif cmd in ("C", "c"):
            x1, y1, x2, y2, x, y = read_nums(6)
            if cmd == "c":
                x1 += pos[0]; y1 += pos[1]
                x2 += pos[0]; y2 += pos[1]
                x += pos[0]; y += pos[1]
            current.extend(_flatten_cubic(pos, (x1, y1), (x2, y2), (x, y)))
            pos = (x, y)
        elif cmd in ("Q", "q"):
            x1, y1, x, y = read_nums(4)
            if cmd == "q":
                x1 += pos[0]; y1 += pos[1]
                x += pos[0]; y += pos[1]
            current.extend(_flatten_quadratic(pos, (x1, y1), (x, y)))
            pos = (x, y)
        elif cmd in ("Z", "z"):
            if current and current[0] != pos:
                current.append(start)
            pos = start
            # The command token was already consumed at the top of the loop.
            # Skipping another one here swallowed the next subpath's moveto,
            # which drew a stray line across the letter.
            cmd = None
        else:
            # Unexpected token; skip to avoid infinite loop.
            i += 1

    if current:
        strokes.append(current)
    return strokes


@lru_cache(maxsize=None)
def load_font(name: str) -> Font:
    """Load a font by file stem (e.g. 'HersheySansMed') from fonts/*.svg."""
    path = FONTS_DIR / f"{name}.svg"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in FONTS_DIR.glob("*.svg")))
        raise FileNotFoundError(f"Font '{name}' not found in {FONTS_DIR}. Available: {available}")

    tree = etree.parse(str(path))
    root = tree.getroot()
    font_el = root.find(f".//{_tag('font')}")
    if font_el is None:
        raise ValueError(f"No <font> element found in {path}")

    default_adv = float(font_el.get("horiz-adv-x", "1000"))

    face_el = font_el.find(_tag("font-face"))
    units_per_em = float(face_el.get("units-per-em", "1000")) if face_el is not None else 1000.0
    ascent = float(face_el.get("ascent", "800")) if face_el is not None else 800.0
    descent = float(face_el.get("descent", "-200")) if face_el is not None else -200.0

    glyphs: dict[str, Glyph] = {}
    for glyph_el in font_el.findall(_tag("glyph")):
        unicode_attr = glyph_el.get("unicode")
        if unicode_attr is None:
            continue
        adv = float(glyph_el.get("horiz-adv-x", default_adv))
        d = glyph_el.get("d", "")
        strokes = parse_path(d)
        glyphs[unicode_attr] = Glyph(unicode=unicode_attr, horiz_adv_x=adv, strokes=strokes)

    if " " not in glyphs:
        glyphs[" "] = Glyph(unicode=" ", horiz_adv_x=default_adv, strokes=[])

    return Font(
        name=name,
        units_per_em=units_per_em,
        ascent=ascent,
        descent=descent,
        default_horiz_adv_x=default_adv,
        glyphs=glyphs,
    )


def list_fonts() -> list[str]:
    return sorted(p.stem for p in FONTS_DIR.glob("*.svg"))
