"""Pass-through mode: load an existing hand-made SVG (paths/lines/
polylines/rects), scale it to fit the target page, and hand back strokes
in the same page-space mm format layout.py produces, so it can go
straight through gcode.py.

Only stroke geometry matters here (fills are ignored - the plotter can
only trace outlines), so this covers the common cases: <path>, <line>,
<polyline>, <polygon>, <rect>.
"""

from __future__ import annotations

import math
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


# 'A'/'a' were missing here, so an arc's command letter was dropped and its
# seven numbers were fed to whatever command came before it - which drew
# garbage rather than the documented straight-line approximation.
_TOKEN_RE = re.compile(r"[MLHVCSQTZAmlhvcsqtza]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _flatten_cubic(p0, p1, p2, p3, segments=16):
    pts = []
    for i in range(1, segments + 1):
        t = i / segments
        mt = 1 - t
        x = (mt**3) * p0[0] + 3 * (mt**2) * t * p1[0] + 3 * mt * (t**2) * p2[0] + (t**3) * p3[0]
        y = (mt**3) * p0[1] + 3 * (mt**2) * t * p1[1] + 3 * mt * (t**2) * p2[1] + (t**3) * p3[1]
        pts.append((x, y))
    return pts


def _flatten_arc(p0, rx, ry, phi_deg, large_arc, sweep, p1, segments=24):
    """Endpoint-parameterised SVG arc -> polyline (SVG spec F.6.5).

    Arcs used to collapse to a straight line to the endpoint, so a rounded
    corner or a circle drawn as two arcs came out as a diamond.
    """
    x1, y1 = p0
    x2, y2 = p1
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [p1]

    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2

    # Scale the radii up if they are too small to span the two endpoints.
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large_arc == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        mod = math.sqrt((ux * ux + uy * uy) * (vx * vx + vy * vy))
        if mod == 0:
            return 0.0
        a = math.acos(max(-1.0, min(1.0, dot / mod)))
        return -a if ux * vy - uy * vx < 0 else a

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = angle(1.0, 0.0, ux, uy)
    dtheta = angle(ux, uy, vx, vy)
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi

    steps = max(2, int(segments * abs(dtheta) / (2 * math.pi)) + 1)
    pts = []
    for i in range(1, steps + 1):
        t = theta1 + dtheta * i / steps
        px = cx + rx * math.cos(t) * cos_p - ry * math.sin(t) * sin_p
        py = cy + rx * math.cos(t) * sin_p + ry * math.sin(t) * cos_p
        pts.append((px, py))
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
        return len(tok) == 1 and tok in "MLHVCSQTZAmlhvcsqtza"

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
            rx, ry, rot, large_arc, sweep = nums(5)
            x, y = nums(2)
            if rel:
                x += pos[0]; y += pos[1]
            current.extend(
                _flatten_arc(pos, rx, ry, rot, bool(large_arc), bool(sweep), (x, y))
            )
            pos = (x, y)
        elif base == "Z":
            if current and current[0] != pos:
                current.append(start)
            pos = start
            # The command token was already consumed at the top of the loop;
            # skipping another one swallowed the next subpath's moveto, which
            # joined separate subpaths with a line the artwork never had.
            cmd = None
        else:
            i += 1

    if current:
        strokes.append(current)
    return strokes


_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")

# Containers whose children define reusable or clipping geometry rather than
# anything that gets drawn.
_NON_RENDERED_TAGS = {"defs", "clipPath", "mask", "symbol", "marker", "pattern"}


def _parse_transform(value: str) -> tuple[float, float, float, float, float, float]:
    """An SVG transform list as an affine matrix (a, b, c, d, e, f).

    Ignoring these plotted grouped or moved artwork in the wrong place -
    Inkscape puts a translate on almost every layer.
    """
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, raw in _TRANSFORM_RE.findall(value or ""):
        args = [float(v) for v in re.split(r"[\s,]+", raw.strip()) if v]
        if name == "matrix" and len(args) == 6:
            t = tuple(args)
        elif name == "translate":
            t = (1.0, 0.0, 0.0, 1.0, args[0], args[1] if len(args) > 1 else 0.0)
        elif name == "scale":
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate":
            ang = math.radians(args[0])
            cos_a, sin_a = math.cos(ang), math.sin(ang)
            t = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(args) == 3:  # rotate about a point
                cx, cy = args[1], args[2]
                t = _multiply(_multiply((1.0, 0.0, 0.0, 1.0, cx, cy), t),
                              (1.0, 0.0, 0.0, 1.0, -cx, -cy))
        elif name == "skewX":
            t = (1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
        elif name == "skewY":
            t = (1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = _multiply(m, t)
    return m


def _multiply(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _element_matrix(el) -> tuple[float, float, float, float, float, float]:
    """The element's transform composed with every ancestor's."""
    chain = []
    node = el
    while node is not None:
        value = node.get("transform") if hasattr(node, "get") else None
        if value:
            chain.append(value)
        node = node.getparent()
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for value in reversed(chain):
        m = _multiply(m, _parse_transform(value))
    return m


def _apply(m, pts):
    a, b, c, d, e, f = m
    return [(a * x + c * y + e, b * x + d * y + f) for x, y in pts]


def _is_hidden(el) -> bool:
    node = el
    while node is not None:
        if not hasattr(node, "get"):
            break
        if etree.QName(node).localname in _NON_RENDERED_TAGS:
            return True
        style = (node.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style:
            return True
        if (node.get("display") or "").lower() == "none":
            return True
        node = node.getparent()
    return False


def _ellipse_to_strokes(cx, cy, rx, ry, segments=64) -> list[list[tuple[float, float]]]:
    pts = []
    for i in range(segments + 1):
        t = 2 * math.pi * i / segments
        pts.append((cx + rx * math.cos(t), cy + ry * math.sin(t)))
    return [pts]


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

    # "100%" and other percentage sizes cannot be turned into mm, and made the
    # whole upload fail; the viewBox (or the default) is the sensible answer.
    if width_attr and width_attr.strip().endswith("%"):
        width_attr = None
    if height_attr and height_attr.strip().endswith("%"):
        height_attr = None

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
    skipped: dict[str, int] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue  # comments and processing instructions
        tag = etree.QName(el).localname
        if tag in ("svg", "g", "defs", "clipPath", "mask", "symbol", "marker",
                   "pattern", "title", "desc", "metadata", "style", "namedview"):
            continue
        if _is_hidden(el):
            continue

        here: list[list[tuple[float, float]]] = []
        if tag == "path" and el.get("d"):
            here = parse_path_d(el.get("d"))
        elif tag == "rect":
            here = _rect_to_strokes(el)
        elif tag == "line":
            x1, y1 = float(el.get("x1", 0)), float(el.get("y1", 0))
            x2, y2 = float(el.get("x2", 0)), float(el.get("y2", 0))
            here = [[(x1, y1), (x2, y2)]]
        elif tag == "polyline":
            pts = _points_attr(el)
            here = [pts] if pts else []
        elif tag == "polygon":
            pts = _points_attr(el)
            here = [pts + [pts[0]]] if pts else []
        elif tag == "circle":
            r = float(el.get("r", 0))
            here = _ellipse_to_strokes(float(el.get("cx", 0)), float(el.get("cy", 0)), r, r)
        elif tag == "ellipse":
            here = _ellipse_to_strokes(float(el.get("cx", 0)), float(el.get("cy", 0)),
                                       float(el.get("rx", 0)), float(el.get("ry", 0)))
        elif tag in ("text", "tspan", "image", "use", "foreignObject"):
            # A plotter can only trace outlines. <text> in particular has to be
            # converted to paths first ("Object to path" in Inkscape).
            skipped[tag] = skipped.get(tag, 0) + 1
            continue
        else:
            continue

        matrix = _element_matrix(el)
        raw_strokes.extend(_apply(matrix, stroke) for stroke in here if stroke)

    strokes_mm = [[(x * unit_to_mm, y * unit_to_mm) for x, y in s] for s in raw_strokes]

    warnings = [
        f"{count} <{tag}> element(s) skipped - a plotter can only draw outlines"
        + (" (use Path > Object to Path in Inkscape)" if tag in ("text", "tspan") else "")
        for tag, count in sorted(skipped.items())
    ]

    if not strokes_mm:
        warnings.append("No plottable geometry found in that SVG.")
        return PlacedText(strokes=[], page_index=0, warnings=warnings)

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
    return PlacedText(strokes=placed, page_index=0, warnings=warnings)
