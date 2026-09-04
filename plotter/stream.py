"""Stream G-code to the GRBL controller over serial, replacing Universal
G-code Sender: simple line-by-line send-and-wait-for-ok, the same
protocol UGS's "Simple Send" mode uses, which GRBL boards handle
reliably without needing to track the 128-byte serial buffer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import serial
import serial.tools.list_ports

_COORD_RE = re.compile(r"([XY])(-?[\d.]+)")


def list_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]


@dataclass
class StreamProgress:
    line_no: int
    total_lines: int
    text: str
    response: str


class GrblError(RuntimeError):
    pass


class GrblStreamer:
    def __init__(self, port: str, baud: int = 115200, timeout: float = 5.0, transport=None):
        """`transport` lets tests/simulation swap in a fake serial device -
        anything with pyserial's .write()/.readline()/.reset_input_buffer()/
        .is_open/.close() surface. Real hardware use leaves it None."""
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._ser = transport
        self._owns_transport = transport is None
        self._paused = False
        self._cancel = False
        # Best-effort position tracking for jogging - GRBL is the real
        # source of truth, this just lets the UI show where it thinks the
        # pen is between explicit '?' status queries.
        self._x = 0.0
        self._y = 0.0
        self._pen_down = False
        self._absolute = True

    def connect(self) -> str:
        if self._owns_transport:
            self._ser = serial.Serial(self.port, self.baud, timeout=self.timeout)
            time.sleep(2.0)  # real GRBL resets the Arduino on port open
        self._ser.reset_input_buffer()
        self._ser.write(b"\r\n\r\n")
        if self._owns_transport:
            time.sleep(0.5)
        self._ser.reset_input_buffer()
        banner_lines = []
        self._ser.write(b"$$\n")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            line = self._ser.readline().decode(errors="replace").strip()
            if line:
                banner_lines.append(line)
            if line.lower().startswith("ok") or line.lower().startswith("error"):
                break
        return "\n".join(banner_lines)

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()

    def unlock(self):
        self._send_and_wait("$X")

    @property
    def position(self) -> tuple[float, float]:
        return self._x, self._y

    @property
    def is_pen_down(self) -> bool:
        return self._pen_down

    def _track_line(self, upper: str):
        """Best-effort local position/pen tracking so the UI can show live
        progress during stream() too, not just during jog(). Mirrors the
        same simple parsing FakeGrblPort uses; real GRBL is still the only
        authority, this is just for display."""
        if upper.startswith("M03"):
            self._pen_down = True
            return
        if upper.startswith("M05"):
            self._pen_down = False
            return
        if upper.startswith("G91"):
            self._absolute = False
            return
        if upper.startswith("G90"):
            self._absolute = True
            return
        if upper.startswith(("G0", "G1")):
            params = dict(_COORD_RE.findall(upper))
            if self._absolute:
                if "X" in params:
                    self._x = float(params["X"])
                if "Y" in params:
                    self._y = float(params["Y"])
            else:
                self._x += float(params.get("X", 0))
                self._y += float(params.get("Y", 0))

    def home(self):
        """Send GRBL's homing cycle. Only works if the machine actually has
        homing switches wired and $22=1 - if not, GRBL returns an alarm."""
        self._send_and_wait("$H")
        self._x, self._y = 0.0, 0.0

    def set_zero(self):
        """Declare the current physical position as (0, 0) without moving.

        On a machine with no limit switches and no encoders this is the only
        way to establish a reference - including after the gantry has been
        pushed by hand, which nothing can detect (see go_to_zero)."""
        self._send_and_wait("G92 X0 Y0")
        self._x, self._y = 0.0, 0.0

    def go_to_zero(self, feed: int = 3000):
        """Travel back to the current zero. This is NOT homing: there are no
        switches to seek, so it only returns to wherever set_zero() last
        declared. If the gantry was moved by hand since, that reference is
        stale and this will go to the wrong place."""
        self._send_and_wait("G90")
        self._send_and_wait(f"G1 X0 Y0 F{feed}")
        self._x, self._y = 0.0, 0.0

    def pen_up(self, servo_cmd: str = "M05", servo_value: int = 10):
        self._send_and_wait(f"{servo_cmd} S{servo_value}")
        self._pen_down = False

    def pen_down(self, servo_cmd: str = "M03", servo_value: int = 50):
        self._send_and_wait(f"{servo_cmd} S{servo_value}")
        self._pen_down = True

    def jog(self, dx: float, dy: float, feed: int = 3000) -> tuple[float, float]:
        """Relative move by (dx, dy) mm. Returns the streamer's best-effort
        new position estimate."""
        self._send_and_wait("G91")
        self._send_and_wait(f"G1 X{dx:.4f} Y{dy:.4f} F{feed}")
        self._send_and_wait("G90")
        self._x += dx
        self._y += dy
        return self._x, self._y

    def _send_and_wait(self, line: str) -> str:
        assert self._ser is not None
        self._ser.write((line + "\n").encode())
        while True:
            resp = self._ser.readline().decode(errors="replace").strip()
            if not resp:
                continue
            if resp.lower().startswith("ok"):
                return resp
            if resp.lower().startswith("error") or resp.lower().startswith("alarm"):
                raise GrblError(f"GRBL rejected '{line}': {resp}")
            # status/info lines ('<Idle...' or '[...]') - keep waiting for ok

    @staticmethod
    def _clean_lines(gcode: str) -> list[str]:
        out = []
        for raw in gcode.splitlines():
            line = raw.split(";", 1)[0].strip()
            if line:
                out.append(line)
        return out

    def pause(self):
        self._paused = True
        if self._ser:
            self._ser.write(b"!")

    def resume(self):
        self._paused = False
        if self._ser:
            self._ser.write(b"~")

    def cancel(self):
        self._cancel = True

    def stream(
        self,
        gcode: str,
        on_progress: Callable[[StreamProgress], None] | None = None,
    ) -> bool:
        """Streams every line, waiting for GRBL's 'ok' between each.
        Returns True if the job completed normally, False if cancel() cut
        it short - so callers can tell a finished job from an abandoned one
        instead of both looking like silent completion."""
        if self._ser is None:
            raise RuntimeError("Not connected - call connect() first")

        lines = self._clean_lines(gcode)
        total = len(lines)
        self._cancel = False

        for i, line in enumerate(lines, start=1):
            if self._cancel:
                self._ser.write(b"!")
                return False
            while self._paused:
                time.sleep(0.1)

            response = self._send_and_wait(line)
            self._track_line(line.upper())
            if on_progress:
                on_progress(StreamProgress(line_no=i, total_lines=total, text=line, response=response))
        return True
