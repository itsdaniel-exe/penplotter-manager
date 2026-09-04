"""Convert placed pen strokes (page-space mm) into G-code for this shop's
GRBL + servo pen-lift plotter. Command dialect (M03/M05 servo control,
G4 dwell, F10000 travel / F2500 draw) matches the real gcode files under
RUN PEN PLOTTER/Gcode/*.gcode, produced by the 4xiDraw "Pen (Servo)"
Inkscape extension.
"""

from __future__ import annotations

from .config import MachineConfig
from .layout import PlacedText


def _fmt(n: float) -> str:
    return f"{n:.4f}".rstrip("0").rstrip(".")


def strokes_to_gcode(
    page: PlacedText,
    page_height_mm: float,
    machine: MachineConfig,
    page_width_mm: float | None = None,
) -> str:
    up_cmd, up_val = machine.pen_up_cmd_value
    down_cmd, down_val = machine.pen_down_cmd_value

    lines: list[str] = []
    lines.append("G90")
    lines.append("G21")
    lines.append(f"{up_cmd} S{up_val}")
    lines.append(f"G4 P{machine.dwell_s}")
    lines.append(f"G1 F{machine.travel_feed}")

    # Page space is top-left origin, y down (reading order). Which physical
    # direction that maps to depends on how the machine is built and how the
    # paper is loaded, so both axes are flippable.
    if machine.invert_x and page_width_mm is None:
        raise ValueError("invert_x needs page_width_mm to mirror against")

    def to_machine(px: float, py: float) -> tuple[float, float]:
        mx = machine.origin_x_mm + (page_width_mm - px if machine.invert_x else px)
        my_from_top = py
        my = machine.origin_y_mm + (page_height_mm - my_from_top if machine.invert_y else my_from_top)
        return mx, my

    for stroke in page.strokes:
        if not stroke:
            continue
        x0, y0 = to_machine(*stroke[0])
        lines.append(f"G1 F{machine.travel_feed}")
        lines.append(f"G1 X{_fmt(x0)} Y{_fmt(y0)}")
        lines.append(f"{down_cmd} S{down_val}")
        lines.append(f"G4 P{machine.dwell_s}")
        lines.append(f"G1 F{machine.draw_feed}")
        for px, py in stroke[1:]:
            mx, my = to_machine(px, py)
            lines.append(f"G1 X{_fmt(mx)} Y{_fmt(my)}")
        lines.append(f"{up_cmd} S{up_val}")
        lines.append(f"G4 P{machine.dwell_s}")

    lines.append(f"G1 F{machine.travel_feed}")
    lines.append(f"G1 X{_fmt(machine.origin_x_mm)} Y{_fmt(machine.origin_y_mm)}")
    return "\n".join(lines) + "\n"


def pages_to_gcode(
    pages: list[PlacedText],
    page_height_mm: float,
    machine: MachineConfig,
    page_width_mm: float | None = None,
) -> list[str]:
    """Returns one gcode string per page."""
    return [strokes_to_gcode(p, page_height_mm, machine, page_width_mm) for p in pages]
