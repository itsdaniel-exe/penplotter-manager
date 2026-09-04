"""A fake GRBL controller that speaks the same wire protocol as the real
board, so the streaming pipeline (connect/handshake/line-by-line ack/
pause/resume/cancel/error handling) can be exercised end-to-end without
hardware. Also records a timestamped trace of the simulated pen path for
playback/animation and flags moves that would exceed a given bed size.

Drop-in replacement for pyserial's Serial object: implements
.write()/.readline()/.reset_input_buffer()/.is_open/.close().
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass
class TracePoint:
    t: float          # seconds since job start
    x: float
    y: float
    pen_down: bool


@dataclass
class SimResult:
    trace: list[TracePoint]
    total_time_s: float
    total_travel_mm: float
    total_draw_mm: float
    out_of_bounds: list[str]
    errors: list[str]


_COORD_RE = re.compile(r"([XYFSP])(-?[\d.]+)")


class FakeGrblPort:
    def __init__(self, bed_width_mm: float | None = None, bed_height_mm: float | None = None,
                 speed_factor: float | None = None):
        """`speed_factor`: if set, actually sleeps a scaled-down fraction of
        each move's real duration (real_seconds / speed_factor), so a live
        "Run" against this fake port behaves like a real job in fast-forward
        instead of completing instantly - which matters because pause/
        resume/cancel need a real time window to be clickable, and the live
        position readout should visibly move. Leave None for the fast,
        instant bulk simulation used by `plot simulate` / offline stats."""
        self.bed_width_mm = bed_width_mm
        self.bed_height_mm = bed_height_mm
        self.speed_factor = speed_factor
        self.is_open = True

        self._queue: list[bytes] = []
        self._x = 0.0
        self._y = 0.0
        self._feed = 1000.0
        self._pen_down = False
        self._absolute = True
        self._t = 0.0

        self.trace: list[TracePoint] = [TracePoint(0.0, 0.0, 0.0, False)]
        self.total_travel_mm = 0.0
        self.total_draw_mm = 0.0
        self.out_of_bounds: list[str] = []
        self.errors: list[str] = []

    # -- pyserial-compatible surface -------------------------------------

    def reset_input_buffer(self):
        self._queue.clear()

    def close(self):
        self.is_open = False

    def write(self, data: bytes):
        text = data.decode(errors="replace").strip()
        if not text:
            return
        if text == "$$":
            self._queue.extend(self._settings_dump())
            return
        if text in ("!", "~", "\x18"):
            self._queue.append(b"ok\r\n")
            return
        self._handle_line(text)

    def readline(self) -> bytes:
        if self._queue:
            return self._queue.pop(0)
        return b""

    # -- simulation --------------------------------------------------------

    def _settings_dump(self) -> list[bytes]:
        lines = [
            b"$0=10\r\n", b"$1=25\r\n", b"$2=0\r\n", b"$3=0\r\n",
            b"$100=80.000\r\n", b"$101=80.000\r\n", b"$102=250.000\r\n",
            b"$110=10000.000\r\n", b"$111=10000.000\r\n",
        ]
        if self.bed_width_mm:
            lines.append(f"$130={self.bed_width_mm:.3f}\r\n".encode())
        if self.bed_height_mm:
            lines.append(f"$131={self.bed_height_mm:.3f}\r\n".encode())
        lines.append(b"ok\r\n")
        return lines

    def _sleep_scaled(self, seconds: float):
        if self.speed_factor and seconds > 0:
            time.sleep(min(seconds / self.speed_factor, 0.25))

    def _handle_line(self, line: str):
        upper = line.upper()

        if upper.startswith("M03"):
            self._pen_down = True
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith("M05"):
            self._pen_down = False
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith("G4"):
            m = re.search(r"P([\d.]+)", upper)
            if m:
                self._t += float(m.group(1))
                self._sleep_scaled(float(m.group(1)))
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith("G90"):
            self._absolute = True
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith("G91"):
            self._absolute = False
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith(("G20", "G21")):
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith("$H"):
            self._x, self._y = 0.0, 0.0
            self._t += 1.0  # homing takes real time on a real machine
            self._sleep_scaled(1.0)
            self.trace.append(TracePoint(self._t, self._x, self._y, self._pen_down))
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith("G92"):
            params = dict(_COORD_RE.findall(upper))
            if "X" in params:
                self._x = float(params["X"])
            if "Y" in params:
                self._y = float(params["Y"])
            self.trace.append(TracePoint(self._t, self._x, self._y, self._pen_down))
            self._queue.append(b"ok\r\n")
            return
        if upper.startswith(("G0", "G1")):
            self._move(upper)
            return

        # Unknown command - ack it so the stream doesn't stall.
        self._queue.append(b"ok\r\n")

    def _move(self, upper: str):
        params = dict(_COORD_RE.findall(upper))
        if self._absolute:
            new_x = float(params["X"]) if "X" in params else self._x
            new_y = float(params["Y"]) if "Y" in params else self._y
        else:
            new_x = self._x + (float(params["X"]) if "X" in params else 0.0)
            new_y = self._y + (float(params["Y"]) if "Y" in params else 0.0)
        if "F" in params:
            self._feed = float(params["F"])

        if self.bed_width_mm is not None and not (0 <= new_x <= self.bed_width_mm):
            self.out_of_bounds.append(f"X={new_x:.1f} outside 0..{self.bed_width_mm}mm bed width")
        if self.bed_height_mm is not None and not (0 <= new_y <= self.bed_height_mm):
            self.out_of_bounds.append(f"Y={new_y:.1f} outside 0..{self.bed_height_mm}mm bed height")

        dist = ((new_x - self._x) ** 2 + (new_y - self._y) ** 2) ** 0.5
        dt = (dist / self._feed) * 60.0 if self._feed > 0 else 0.0
        self._t += dt
        self._sleep_scaled(dt)

        if self._pen_down:
            self.total_draw_mm += dist
        else:
            self.total_travel_mm += dist

        self._x, self._y = new_x, new_y
        self.trace.append(TracePoint(self._t, self._x, self._y, self._pen_down))
        self._queue.append(b"ok\r\n")

    def result(self) -> SimResult:
        return SimResult(
            trace=self.trace,
            total_time_s=self._t,
            total_travel_mm=self.total_travel_mm,
            total_draw_mm=self.total_draw_mm,
            out_of_bounds=self.out_of_bounds,
            errors=self.errors,
        )
