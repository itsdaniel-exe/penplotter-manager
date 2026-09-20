"""Regression tests for the machine-specific facts established by calibrating
against the real plotter. Each of these was a real bug or a real physical
discovery - if one starts failing, the machine will misbehave in a way that is
slow and expensive to diagnose from the symptoms alone.

Plain Python, no test framework needed:

    .venv\\Scripts\\python -m tests.test_calibration
"""

from __future__ import annotations

import re

from ._harness import RecordingPort, check, report, run

from plotter import server as srv
from plotter.config import MachineConfig
from plotter.gcode import strokes_to_gcode
from plotter.layout import PlacedText
from plotter.stream import GrblError, GrblStreamer

# ------------------------------------------------------------ orientation --

def test_page_orientation():
    """Page space is top-left origin, y down. invert_x/invert_y decide which
    physical corner that maps to. Confirmed on the real machine: X+ runs left
    and Y+ runs toward the operator, so pages mirror in X but not Y."""
    print("page orientation (invert_x / invert_y)")
    W, H = 100.0, 200.0
    placed = PlacedText(strokes=[[(0.0, 0.0), (10.0, 0.0)]], page_index=0)

    def first_move(machine: MachineConfig) -> tuple[float, float]:
        for line in strokes_to_gcode(placed, H, machine, W).splitlines():
            m = re.match(r"G1 X(-?[\d.]+) Y(-?[\d.]+)$", line)
            if m:
                return float(m.group(1)), float(m.group(2))
        raise AssertionError("no positioning move in generated gcode")

    check("no flip: page top-left -> machine (0, 0)",
          first_move(MachineConfig(invert_x=False, invert_y=False)) == (0.0, 0.0))
    check("flip Y: page top-left -> machine (0, H)",
          first_move(MachineConfig(invert_x=False, invert_y=True)) == (0.0, 200.0))
    check("flip X: page top-left -> machine (W, 0)",
          first_move(MachineConfig(invert_x=True, invert_y=False)) == (100.0, 0.0))
    check("flip both: page top-left -> machine (W, H)",
          first_move(MachineConfig(invert_x=True, invert_y=True)) == (100.0, 200.0))

    # Mirroring needs the page width. Failing loudly beats silently not
    # mirroring, which would print every job backwards.
    try:
        strokes_to_gcode(placed, H, MachineConfig(invert_x=True), None)
    except ValueError:
        check("flip X without a page width raises rather than silently not mirroring", True)
    else:
        check("flip X without a page width raises rather than silently not mirroring", False)


# -------------------------------------------------------------------- pen --

def test_pen_mapping():
    """This build's servo rest state (M05, which ignores its S value) is
    physically DOWN, not up - the reverse of what the original files implied.
    swap_pen picks which command+value pair means 'up'."""
    print("pen up/down mapping (swap_pen)")
    normal, swapped = MachineConfig(swap_pen=False), MachineConfig(swap_pen=True)

    check("unswapped: up is M05 S10", normal.pen_up_cmd_value == ("M05", 10))
    check("unswapped: down is M03 S50", normal.pen_down_cmd_value == ("M03", 50))
    check("swapped: up is M03 S50", swapped.pen_up_cmd_value == ("M03", 50))
    check("swapped: down is M05 S10", swapped.pen_down_cmd_value == ("M05", 10))

    # The swap has to reach real job gcode, not just the jog buttons - a job
    # run with the wrong mapping either never touches the paper or drags the
    # pen through every travel move.
    placed = PlacedText(strokes=[[(0.0, 0.0), (10.0, 0.0)]], page_index=0)
    head_normal = strokes_to_gcode(placed, 200.0, normal, 100.0).splitlines()[2]
    head_swapped = strokes_to_gcode(placed, 200.0, swapped, 100.0).splitlines()[2]
    check("job gcode honours the swap", head_normal == "M05 S10" and head_swapped == "M03 S50",
          f"got {head_normal!r} / {head_swapped!r}")

    # The default must match the real machine, and the real historical
    # bounndrycreation1_*.gcode files: M03 before travel, M05 before drawing.
    check("default config matches the real machine (M03 lifts)",
          MachineConfig().pen_up_cmd_value == ("M03", 50))


def test_servo_values_are_sent():
    """The Servo up/down fields have to reach the machine, or pen height
    cannot be tuned - they were previously ignored by the jog buttons."""
    print("servo values reach the machine")
    port = RecordingPort()
    s = GrblStreamer(port="FAKE", transport=port)
    s.pen_up("M05", 25)
    s.pen_down("M03", 70)
    check("custom servo values are emitted verbatim",
          port.written == ["M05 S25", "M03 S70"], str(port.written))


# --------------------------------------------------------- motion helpers --

def test_go_to_zero():
    """This machine has no limit switches, so $H can only error. 'Go to zero'
    returns to the last set_zero() reference instead."""
    print("go to zero (stands in for homing)")
    port = RecordingPort()
    s = GrblStreamer(port="FAKE", transport=port)
    s.jog(30, -12)
    check("jog tracks position", s.position == (30, -12), str(s.position))

    port.written.clear()
    s.go_to_zero(feed=3000)
    # The pen lifts first: this can be a full-page diagonal, and doing it with
    # the pen down rules a line straight across the operator's sheet.
    check("the pen is lifted before the travel move",
          port.written == ["M05 S10", "G90", "G1 X0 Y0 F3000"], str(port.written))
    check("position resets to origin", s.position == (0.0, 0.0), str(s.position))


# ------------------------------------------------------------ serial port --

def test_serial_port_ownership():
    """Windows allows one handle per COM port. Every browser tab gets its own
    Session, so without handover a stale tab locks the user out of their own
    machine - which happened twice during calibration."""
    print("serial port ownership")

    class FakeStreamer:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    a, b, c, d = (srv.Session() for _ in range(4))
    a.streamer, b.streamer = FakeStreamer(), FakeStreamer()

    check("first claim displaces nobody", srv._take_port_ownership(a) is False)
    took_over = srv._take_port_ownership(b)
    check("second session takes the port over", took_over is True)
    check("previous owner is disconnected", a.streamer is None)
    check("same session reconnecting is not a takeover", srv._take_port_ownership(b) is False)

    srv._release_port_ownership(b)
    check("release clears ownership", srv._port_owner is None)

    srv._take_port_ownership(c)
    srv._release_port_ownership(d)
    check("a non-owner cannot release someone else's port", srv._port_owner is c)
    srv._release_port_ownership(c)


def test_silent_board_does_not_hang():
    """'Zero here' once waited forever for an 'ok' the board never sent, so
    the whole console froze. A reply has to arrive in time or it's an error."""
    print("board that stops answering")

    class SilentPort(RecordingPort):
        def readline(self) -> bytes:
            return b""

    s = GrblStreamer(port="FAKE", transport=SilentPort(), reply_timeout=0.2)
    try:
        s.set_zero()
        check("no reply raises instead of hanging", False, "set_zero returned")
    except GrblError as e:
        check("no reply raises instead of hanging", "No reply" in str(e), str(e))

    class ClosedMidRead(RecordingPort):
        def readline(self) -> bytes:
            # what pyserial on Windows does when another tab closes the handle
            self.is_open = False
            raise AttributeError("'NoneType' object has no attribute 'hEvent'")

    s = GrblStreamer(port="FAKE", transport=ClosedMidRead())
    try:
        s.set_zero()
        check("port closed mid-read gives a readable error", False, "set_zero returned")
    except GrblError as e:
        check("port closed mid-read gives a readable error", "Connection closed" in str(e), str(e))


def test_cancel_stops_cleanly():
    """Cancel used to hang for good if the job was paused, and otherwise left
    real GRBL frozen in a feed hold with moves still queued."""
    print("cancel")
    import threading
    import time

    # The pause has to land *during* the stream: stream() deliberately clears a
    # stale pause left set by an earlier job before it sends its first line.
    class PauseAfterFirstLine(RecordingPort):
        def write(self, data: bytes) -> None:
            super().write(data)
            if len(self.written) == 1:
                s.pause()

    port = PauseAfterFirstLine()
    s = GrblStreamer(port="FAKE", transport=port)
    result = []
    t = threading.Thread(target=lambda: result.append(s.stream("G1 X1\nG1 X2\nG1 X3\n")), daemon=True)
    t.start()
    time.sleep(0.3)
    check("a paused job holds instead of finishing", not result, str(result))
    s.cancel()
    t.join(timeout=2)
    check("cancel while paused doesn't hang", not t.is_alive())
    check("a cancelled job reports it didn't finish", result == [False], str(result))
    check("the hold is released so the machine can finish its moves", "~" in port.written, str(port.written))
    check("waits for the machine to stop moving (G4 sync)", port.written[-1:] == ["G4 P0"], str(port.written))
    check("nothing after the paused line is sent", "G1 X2" not in port.written, str(port.written))

    class CancelAfterTwo(RecordingPort):
        def write(self, data: bytes) -> None:
            super().write(data)
            if len(self.written) == 2:
                s2.cancel()

    port2 = CancelAfterTwo()
    s2 = GrblStreamer(port="FAKE", transport=port2)
    done = s2.stream("G1 X1\nG1 X2\nG1 X3\nG1 X4\n")
    check("cancel mid-job stops sending and waits for the machine",
          port2.written == ["G1 X1", "G1 X2", "G4 P0"], str(port2.written))
    check("cancel mid-job reports it didn't finish", done is False)
    check("no feed hold left behind", "!" not in port2.written, str(port2.written))


def test_pause_does_not_time_out():
    """A feed hold stops GRBL acking queued moves. Counting that against the
    no-reply timeout killed a job just because the operator paused it to look
    at the pen."""
    print("a long pause is not a dead board")
    import threading
    import time

    class HeldBoard(RecordingPort):
        """A held GRBL: it stops answering until the hold is released."""

        def write(self, data: bytes) -> None:
            super().write(data)
            if len(self.written) == 1:
                s.pause()

        def readline(self) -> bytes:
            return b"" if s._paused else b"ok"

    port = HeldBoard()
    s = GrblStreamer(port="FAKE", transport=port, reply_timeout=0.3)
    result = []
    t = threading.Thread(target=lambda: result.append(s.stream("G1 X1\nG1 X2")), daemon=True)
    t.start()
    time.sleep(0.9)  # three times the reply timeout, still held
    check("a pause longer than the reply timeout doesn't fail the job", not result, str(result))
    s.resume()
    t.join(timeout=3)
    check("the job carries on after resume", result == [True], str(result))


def test_pen_state_is_not_guessed():
    """Nothing reads the servo back, so before anything drives it the console
    must not claim to know where the pen is - it used to assert "pen up"."""
    print("pen state is known only once driven")
    s = GrblStreamer(port="FAKE", transport=RecordingPort())
    check("pen state starts unknown", s.is_pen_known is False)
    s.pen_down("M05", 10)
    check("driving the servo makes it known", s.is_pen_known is True and s.is_pen_down is True)

    # With swap_pen the job gcode LIFTS with M03, so tracking that assumed
    # M03 = down reported the pen (and the simulator's draw/travel split)
    # backwards for this machine.
    machine = MachineConfig(swap_pen=True)
    s2 = GrblStreamer(port="FAKE", transport=RecordingPort())
    s2.set_pen_mapping(machine.pen_up_cmd_value, machine.pen_down_cmd_value)
    s2._track_line("M03 S50")
    check("M03 is a pen LIFT on this machine", s2.is_pen_down is False)
    s2._track_line("M05 S10")
    check("M05 is a pen DROP on this machine", s2.is_pen_down is True)


def main() -> int:
    run([test_page_orientation, test_pen_mapping, test_servo_values_are_sent,
         test_go_to_zero, test_serial_port_ownership, test_silent_board_does_not_hang,
         test_cancel_stops_cleanly, test_pause_does_not_time_out,
         test_pen_state_is_not_guessed])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
