"""Pass-through mode: load an existing hand-made SVG (paths/lines/
polylines/rects), scale it to fit the target page, and hand back strokes
in the same page-space mm format layout.py produces, so it can go
straight through gcode.py.

Only stroke geometry matters here (fills are ignored - the plotter can
only trace outlines), so this covers the common cases: <path>, <line>,
<polyline>, <polygon>, <rect>.
"""

from __future__ import annotations

import re

from lxml import etree

from .config import PageConfig
from .layout import PlacedText

_SVG_NS = "http://www.w3.org/2000/svg"


def _tag(name: str) -> str:
    return f"{{{_SVG_NS}}}{name}"


_UNIT_RE = re.compile(r"^\s*(-?[\d.]+)\s*(px|mm|cm|in|pt)?\s*$")

_UNIT_TO_MM = {
    "px": 25.4 / 96,
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72,
    None: 25.4 / 96,  # unitless SVG length defaults to px
}


def _length_to_mm(value: str) -> float:
    m = _UNIT_RE.match(value)
    if not m:
        raise ValueError(f"Can't parse SVG length '{value}'")
    num, unit = m.groups()
    return float(num) * _UNIT_TO_MM[unit]


_TOKEN_RE = re.compile(r"[MLHVCSQTZmlhvcsqtz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _flatten_cubic(p0, p1, p2, p3, segments=16):
    pts = []
    for i in range(1, segments + 1):
        t = i / segments
        mt = 1 - t
        x = (mt**3) * p0[0] + 3 * (mt**2) * t * p1[0] + 3 * mt * (t**2) * p2[0] + (t**3) * p3[0]
        y = (mt**3) * p0[1] + 3 * (mt**2) * t * p1[1] + 3 * mt * (t**2) * p2[1] + (t**3) * p3[1]
        pts.append((x, y))
    return pts


def _flatten_quadratic(p0, p1, p2, segments=12):
    pts = []
    for i in range(1, segments + 1):
        t = i / segments
        mt = 1 - t
        x = (mt**2) * p0[0] + 2 * mt * t * p1[0] + (t**2) * p2[0]
        y = (mt**2) * p0[1] + 2 * mt * t * p1[1] + (t**2) * p2[1]
        pts.append((x, y))
    return pts


def parse_path_d(d: str) -> list[list[tuple[float, float]]]:
    """General SVG path parser: M/L/H/V/C/S/Q/T/Z (upper+lower). Arcs (A)
    are approximated as a straight line to the endpoint."""
    tokens = _TOKEN_RE.findall(d)
    strokes: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    pos = (0.0, 0.0)
    start = (0.0, 0.0)
    last_cubic_ctrl: tuple[float, float] | None = None
    last_quad_ctrl: tuple[float, float] | None = None
    i = 0
    cmd = None

    def nums(n):
        nonlocal i
        vals = [float(tokens[i + k]) for k in range(n)]
        i += n
        return vals

    def is_cmd_token(tok):
        return tok in "MLHVCSQTZmlhvcsqtz"

    while i < len(tokens):
        if is_cmd_token(tokens[i]):
            cmd = tokens[i]
            i += 1
        base = cmd.upper() if cmd else None
        rel = cmd is not None and cmd.islower()

        if base == "M":
            x, y = nums(2)
            if rel and current:
                x += pos[0]; y += pos[1]
            if current:
                strokes.append(current)
            pos = (x, y)
            start = pos
            current = [pos]
            cmd = "l" if rel else "L"
        elif base == "L":
            x, y = nums(2)
            if rel:
                x += pos[0]; y += pos[1]
            pos = (x, y)
            current.append(pos)
        elif base == "H":
            (x,) = nums(1)
            if rel:
                x += pos[0]
            pos = (x, pos[1])
            current.append(pos)
        elif base == "V":
            (y,) = nums(1)
            if rel:
                y += pos[1]
            pos = (pos[0], y)
            current.append(pos)
        elif base == "C":
            x1, y1, x2, y2, x, y = nums(6)
            if rel:
                x1 += pos[0]; y1 += pos[1]
                x2 += pos[0]; y2 += pos[1]
                x += pos[0]; y += pos[1]
            current.extend(_flatten_cubic(pos, (x1, y1), (x2, y2), (x, y)))
            last_cubic_ctrl = (x2, y2)
            pos = (x, y)
        elif base == "S":
            x2, y2, x, y = nums(4)
            if rel:
                x2 += pos[0]; y2 += pos[1]
                x += pos[0]; y += pos[1]
            if last_cubic_ctrl:
                x1, y1 = 2 * pos[0] - last_cubic_ctrl[0], 2 * pos[1] - last_cubic_ctrl[1]
            else:
                x1, y1 = pos
            current.extend(_flatten_cubic(pos, (x1, y1), (x2, y2), (x, y)))
            last_cubic_ctrl = (x2, y2)
            pos = (x, y)
        elif base == "Q":
            x1, y1, x, y = nums(4)
            if rel:
                x1 += pos[0]; y1 += pos[1]
                x += pos[0]; y += pos[1]
            current.extend(_flatten_quadratic(pos, (x1, y1), (x, y)))
            last_quad_ctrl = (x1, y1)
            pos = (x, y)
        elif base == "T":
            x, y = nums(2)
            if rel:
                x += pos[0]; y += pos[1]
            if last_quad_ctrl:
                x1, y1 = 2 * pos[0] - last_quad_ctrl[0], 2 * pos[1] - last_quad_ctrl[1]
            else:
                x1, y1 = pos
            current.extend(_flatten_quadratic(pos, (x1, y1), (x, y)))
            last_quad_ctrl = (x1, y1)
            pos = (x, y)
        elif base == "A":
            nums(5)  # rx ry x-axis-rotation large-arc-flag sweep-flag - unused
            x, y = nums(2)
            if rel:
                x += pos[0]; y += pos[1]
            current.append((x, y))  # straight-line approximation
            pos = (x, y)
        elif base == "Z":
            if current and current[0] != pos:
                current.append(start)
            pos = start
            i += 1
        else:
            i += 1

    if current:
        strokes.append(current)
    return strokes


def _rect_to_strokes(el) -> list[list[tuple[float, float]]]:
    x = float(el.get("x", "0"))
    y = float(el.get("y", "0"))
    w = float(el.get("width", "0"))
    h = float(el.get("height", "0"))
    return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]


def _points_attr(el) -> list[tuple[float, float]]:
    raw = el.get("points", "").replace(",", " ").split()
    nums = [float(v) for v in raw]
    return list(zip(nums[0::2], nums[1::2]))


def load_svg_strokes(path: str, page: PageConfig) -> PlacedText:
    """Load strokes from an SVG file and fit them to `page`'s content box,
    centered, preserving aspect ratio."""
    tree = etree.parse(path)
    root = tree.getroot()

    view_box = root.get("viewBox")
    width_attr = root.get("width")
    height_attr = root.get("height")

    if view_box:
        _, _, vb_w, vb_h = (float(v) for v in view_box.replace(",", " ").split())
    elif width_attr and height_attr:
        vb_w = _length_to_mm(width_attr) if not width_attr.replace(".", "", 1).isdigit() else float(width_attr)
        vb_h = _length_to_mm(height_attr) if not height_attr.replace(".", "", 1).isdigit() else float(height_attr)
    else:
        vb_w, vb_h = 1000.0, 1000.0

    # user-units -> mm: if width/height carry real units, scale viewBox
    # units to mm via that; else assume viewBox units already ~= mm.
    if width_attr and view_box:
        doc_w_mm = _length_to_mm(width_attr)
        unit_to_mm = doc_w_mm / vb_w if vb_w else 1.0
    else:
        unit_to_mm = 1.0

    raw_strokes: list[list[tuple[float, float]]] = []
    for el in root.iter():
        tag = etree.QName(el).localname
        if tag == "path" and el.get("d"):
            raw_strokes.extend(parse_path_d(el.get("d")))
        elif tag == "rect":
            raw_strokes.extend(_rect_to_strokes(el))
        elif tag == "line":
            x1, y1 = float(el.get("x1", 0)), float(el.get("y1", 0))
            x2, y2 = float(el.get("x2", 0)), float(el.get("y2", 0))
            raw_strokes.append([(x1, y1), (x2, y2)])
        elif tag == "polyline":
            pts = _points_attr(el)
            if pts:
                raw_strokes.append(pts)
        elif tag == "polygon":
            pts = _points_attr(el)
            if pts:
                raw_strokes.append(pts + [pts[0]])

    strokes_mm = [[(x * unit_to_mm, y * unit_to_mm) for x, y in s] for s in raw_strokes]

    if not strokes_mm:
        return PlacedText(strokes=[], page_index=0)

    xs = [x for s in strokes_mm for x, _ in s]
    ys = [y for s in strokes_mm for _, y in s]
    art_w = max(xs) - min(xs) or 1.0
    art_h = max(ys) - min(ys) or 1.0

    fit_scale = min(page.content_width_mm / art_w, page.content_height_mm / art_h, 1.0)
    off_x = page.margin_left_mm + (page.content_width_mm - art_w * fit_scale) / 2 - min(xs) * fit_scale
    off_y = page.margin_top_mm + (page.content_height_mm - art_h * fit_scale) / 2 - min(ys) * fit_scale

    placed = [
        [(x * fit_scale + off_x, y * fit_scale + off_y) for x, y in s]
        for s in strokes_mm
    ]
    return PlacedText(strokes=placed, page_index=0)
