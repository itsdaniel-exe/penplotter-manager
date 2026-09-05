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
from plotter.stream import GrblStreamer

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
    check("absolute move at an explicit feed",
          port.written == ["G90", "G1 X0 Y0 F3000"], str(port.written))
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


def main() -> int:
    run([test_page_orientation, test_pen_mapping, test_servo_values_are_sent,
         test_go_to_zero, test_serial_port_ownership])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
