"""Generate the app icon (packaging/icon.ico) with no image libraries.

A 256x256 PNG wrapped in an ICO container - Windows has read PNG-compressed
icons since Vista. Drawn rather than shipped as a binary so it can be tweaked
without a graphics program, and so the repo stays readable.

    .venv\\Scripts\\python packaging/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 256
OUT = Path(__file__).resolve().parent / "icon.ico"

# The console's own palette (web/index.html): warm brown on near-black paper.
BG = (26, 31, 30, 255)
PAPER = (247, 245, 238, 255)
INK = (156, 107, 46, 255)
NIB = (224, 180, 110, 255)


def _blend(dst, src, alpha):
    return tuple(round(d + (s - d) * alpha) for d, s in zip(dst[:3], src[:3])) + (255,)


def _rounded_rect(px, x0, y0, x1, y1, radius, colour):
    for y in range(int(y0), int(y1)):
        for x in range(int(x0), int(x1)):
            cx = min(max(x, x0 + radius), x1 - radius)
            cy = min(max(y, y0 + radius), y1 - radius)
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d <= radius:
                # soften the last pixel so the corner doesn't look chewed
                px[y][x] = _blend(px[y][x], colour, min(1.0, radius - d + 1))


def _line(px, x0, y0, x1, y1, width, colour):
    """Anti-aliased thick line by distance to the segment."""
    dx, dy = x1 - x0, y1 - y0
    length_sq = dx * dx + dy * dy
    lo_x, hi_x = int(min(x0, x1) - width - 2), int(max(x0, x1) + width + 2)
    lo_y, hi_y = int(min(y0, y1) - width - 2), int(max(y0, y1) + width + 2)
    for y in range(max(0, lo_y), min(SIZE, hi_y)):
        for x in range(max(0, lo_x), min(SIZE, hi_x)):
            t = 0.0 if not length_sq else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length_sq))
            nx, ny = x0 + t * dx, y0 + t * dy
            d = ((x - nx) ** 2 + (y - ny) ** 2) ** 0.5
            if d <= width:
                px[y][x] = _blend(px[y][x], colour, min(1.0, width - d + 0.5))


def _disc(px, cx, cy, r, colour):
    for y in range(max(0, int(cy - r - 2)), min(SIZE, int(cy + r + 2))):
        for x in range(max(0, int(cx - r - 2)), min(SIZE, int(cx + r + 2))):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d <= r:
                px[y][x] = _blend(px[y][x], colour, min(1.0, r - d + 0.5))


def draw() -> list[list[tuple[int, int, int, int]]]:
    px = [[(0, 0, 0, 0) for _ in range(SIZE)] for _ in range(SIZE)]

    # dark rounded tile
    _rounded_rect(px, 8, 8, SIZE - 8, SIZE - 8, 48, BG)
    # the sheet of paper
    _rounded_rect(px, 52, 40, SIZE - 52, SIZE - 40, 8, PAPER)

    # three strokes of "handwriting" on the page
    _line(px, 74, 96, 150, 96, 5, INK)
    _line(px, 74, 128, 182, 128, 5, INK)
    _line(px, 74, 160, 132, 160, 5, INK)

    # the pen, coming in from the bottom right
    _line(px, 196, 214, 128, 150, 13, INK)
    _disc(px, 126, 148, 9, NIB)
    return px


def png_bytes(px) -> bytes:
    raw = bytearray()
    for row in px:
        raw.append(0)  # filter type 0
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def main() -> None:
    png = png_bytes(draw())
    # ICONDIR + one ICONDIRENTRY, then the PNG itself
    ico = struct.pack("<HHH", 0, 1, 1)
    ico += struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    OUT.write_bytes(ico + png)
    print(f"wrote {OUT} ({len(ico + png) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
