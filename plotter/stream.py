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
    def __init__(self, port: str, baud: int = 115200, timeout: float = 5.0, transport=None,
                 reply_timeout: float = 30.0):
        """`transport` lets tests/simulation swap in a fake serial device -
        anything with pyserial's .write()/.readline()/.reset_input_buffer()/
        .is_open/.close() surface. Real hardware use leaves it None.

        `reply_timeout`: how long to wait for GRBL's 'ok' before giving up."""
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.reply_timeout = reply_timeout
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
        # Nothing reads the servo back, so until we drive it ourselves we are
        # guessing - say so rather than showing a confident "pen up".
        self._pen_known = False
        self._absolute = True
        # Which servo command LOWERS the pen on this machine. With swap_pen
        # the job gcode emits M03 to LIFT, so tracking that assumed M03 = down
        # reported the pen state - and the simulator's draw/travel split -
        # exactly backwards. set_pen_mapping() supplies the real answer.
        self._pen_up_cmd, self._pen_up_val = "M05", 10
        self._pen_down_cmd, self._pen_down_val = "M03", 50

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

    def set_pen_mapping(self, up: tuple[str, int], down: tuple[str, int]) -> None:
        """Tell the streamer which command lifts and which lowers the pen on
        this machine (MachineConfig.pen_up_cmd_value / pen_down_cmd_value)."""
        self._pen_up_cmd, self._pen_up_val = up[0].upper(), up[1]
        self._pen_down_cmd, self._pen_down_val = down[0].upper(), down[1]

    def unlock(self):
        self._send_and_wait("$X")

    @property
    def position(self) -> tuple[float, float]:
        return self._x, self._y

    @property
    def is_pen_down(self) -> bool:
        return self._pen_down

    @property
    def is_cancelled(self) -> bool:
        return self._cancel

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_pen_known(self) -> bool:
        """False until something in this session has actually driven the servo.
        GRBL can't be asked where the pen is, so before that it's a guess."""
        return self._pen_known

    def _track_line(self, upper: str):
        """Best-effort local position/pen tracking so the UI can show live
        progress during stream() too, not just during jog(). Mirrors the
        same simple parsing FakeGrblPort uses; real GRBL is still the only
        authority, this is just for display."""
        if upper.startswith(self._pen_down_cmd):
            self._pen_down = True
            self._pen_known = True
            return
        if upper.startswith(self._pen_up_cmd):
            self._pen_down = False
            self._pen_known = True
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
        stale and this will go to the wrong place.

        The pen is lifted first: this can be a full-page diagonal, and doing it
        with the pen down rules a line straight across the operator's sheet."""
        if self._pen_down or not self._pen_known:
            self.pen_up(self._pen_up_cmd, self._pen_up_val)
        self._send_and_wait("G90")
        self._send_and_wait(f"G1 X0 Y0 F{feed}")
        self._x, self._y = 0.0, 0.0

    def pen_up(self, servo_cmd: str = "M05", servo_value: int = 10):
        self._send_and_wait(f"{servo_cmd} S{servo_value}")
        self._pen_down = False
        self._pen_known = True

    def pen_down(self, servo_cmd: str = "M03", servo_value: int = 50):
        self._send_and_wait(f"{servo_cmd} S{servo_value}")
        self._pen_down = True
        self._pen_known = True

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
        # Without a deadline, a board that never answers hangs this forever,
        # and every button waiting on it looks dead.
        deadline = time.time() + self.reply_timeout
        while True:
            if self._paused:
                # A feed hold stops GRBL acking queued moves. That's the
                # operator's doing, not a dead board - don't time out on it.
                deadline = time.time() + self.reply_timeout
            if time.time() >= deadline:
                break
            try:
                raw = self._ser.readline()
            except Exception:
                # Another tab took the port and closed this handle mid-read.
                # pyserial reports that as a baffling AttributeError.
                if not self._ser.is_open:
                    raise GrblError(f"Connection closed while waiting for a reply to '{line}'") from None
                raise
            resp = raw.decode(errors="replace").strip()
            if not resp:
                continue
            if resp.lower().startswith("ok"):
                return resp
            if resp.lower().startswith("error") or resp.lower().startswith("alarm"):
                raise GrblError(f"GRBL rejected '{line}': {resp}")
            # status/info lines ('<Idle...' or '[...]') - keep waiting for ok
        raise GrblError(
            f"No reply from the board to '{line}' after {self.reply_timeout:.0f}s - "
            "check it's powered and the USB cable is in, then reconnect."
        )

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

    def begin_job(self) -> None:
        """Clear the stop and hold flags before a new job starts.

        A cancelled job leaves `_cancel` set. stream() clears it, but the
        notebook runner asks the operator to fit a pen *before* it streams
        anything, and that wait checks the flag - so the next job cancelled
        itself the instant it started, with nothing in the log to explain why.
        """
        self._cancel = False
        self._paused = False

    def cancel(self):
        """Stop after the moves GRBL has already accepted. Deliberately not a
        soft reset: that also wipes the G92 zero, and with no limit switches
        there's no getting it back."""
        self._cancel = True
        if self._paused:
            # A held machine never drains its buffer, so the stream would
            # wait forever for its next 'ok' - release the hold first.
            self._paused = False
            if self._ser:
                self._ser.write(b"~")

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
        # A pause left set by a previous job would silently hold this one
        # before its first line, with nothing in the UI to explain it.
        self._paused = False

        for i, line in enumerate(lines, start=1):
            while self._paused and not self._cancel:
                time.sleep(0.1)
            if self._cancel:
                # GRBL only acks a dwell once every buffered move has run, so
                # this returns when the machine has actually stopped.
                self._send_and_wait("G4 P0")
                return False

            response = self._send_and_wait(line)
            self._track_line(line.upper())
            if on_progress:
                on_progress(StreamProgress(line_no=i, total_lines=total, text=line, response=response))
        return True
