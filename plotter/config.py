"""Page, text-style and machine configuration with sane defaults pulled
from the shop's real calibration (fonts/HersheySansMed, servo values,
feed rates) found in RUN PEN PLOTTER/Gcode/*.gcode.
"""

from __future__ import annotations

from dataclasses import dataclass

# Common paper sizes in mm (width, height), portrait.
PAGE_SIZES = {
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "a6": (105.0, 148.0),
    "letter": (215.9, 279.4),
    "custom-card": (195.0, 295.0),  # matches "wriiting test testtest.svg"
}


@dataclass
class PageConfig:
    width_mm: float = 210.0
    height_mm: float = 297.0
    margin_top_mm: float = 20.0
    margin_bottom_mm: float = 20.0
    margin_left_mm: float = 15.0
    margin_right_mm: float = 15.0
    landscape: bool = False

    @classmethod
    def from_size(cls, size: str, **overrides) -> "PageConfig":
        key = size.lower()
        if key not in PAGE_SIZES:
            raise ValueError(f"Unknown page size '{size}'. Options: {', '.join(PAGE_SIZES)}")
        w, h = PAGE_SIZES[key]
        return cls(width_mm=w, height_mm=h, **overrides)

    def __post_init__(self):
        if self.landscape and self.width_mm < self.height_mm:
            self.width_mm, self.height_mm = self.height_mm, self.width_mm

    @property
    def content_width_mm(self) -> float:
        return self.width_mm - self.margin_left_mm - self.margin_right_mm

    @property
    def content_height_mm(self) -> float:
        return self.height_mm - self.margin_top_mm - self.margin_bottom_mm


@dataclass
class TextStyle:
    font: str = "HersheySansMed"
    font_size_mm: float = 5.0          # cap-height-ish size, see layout.py
    line_spacing_mm: float = 8.0       # baseline-to-baseline; matches typical ruled paper
    letter_spacing_mm: float = 0.0
    align: str = "left"                # left | center | right | justify
    paragraph_gap_mm: float = 2.0


@dataclass
class MachineConfig:
    port: str = ""                     # e.g. "COM3"; blank = must be given/autodetected
    baud: int = 115200
    servo_up: int = 10                 # S value, pen lifted
    servo_down: int = 50               # S value, pen down (matches real gcode: M03 S50)
    servo_cmd_up: str = "M05"
    servo_cmd_down: str = "M03"
    travel_feed: int = 10000           # mm/min, pen-up rapid
    draw_feed: int = 2500              # mm/min, pen-down drawing
    dwell_s: float = 0.1               # settle time after servo move
    origin_x_mm: float = 0.0
    origin_y_mm: float = 0.0
    # Confirmed on the real machine by test print: X+ runs physically left and
    # Y+ runs toward the operator, so the page needs mirroring in X but not Y.
    invert_y: bool = False             # gcode Y grows downward-to-upward vs page top-down
    invert_x: bool = True              # mirror left-right, if X+ runs the opposite way
    # Confirmed on the real machine: M03 S50 lifts, M05 S10 lowers - the reverse
    # of what the old reverse-engineered files implied, and matching the real
    # bounndrycreation1_*.gcode files (M03 before travel, M05 before drawing).
    swap_pen: bool = True              # swap which command lifts vs lowers the pen

    @property
    def pen_up_cmd_value(self) -> tuple[str, int]:
        return (self.servo_cmd_down, self.servo_down) if self.swap_pen else (self.servo_cmd_up, self.servo_up)

    @property
    def pen_down_cmd_value(self) -> tuple[str, int]:
        return (self.servo_cmd_up, self.servo_up) if self.swap_pen else (self.servo_cmd_down, self.servo_down)
