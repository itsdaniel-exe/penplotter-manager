"""Run every test module:  .venv\\Scripts\\python -m tests"""

from __future__ import annotations

from . import _harness, test_calibration, test_formatting

if __name__ == "__main__":
    print("=== machine calibration ===\n")
    _harness.run([
        test_calibration.test_page_orientation,
        test_calibration.test_pen_mapping,
        test_calibration.test_servo_values_are_sent,
        test_calibration.test_go_to_zero,
        test_calibration.test_serial_port_ownership,
    ])
    print("=== text formatting ===\n")
    _harness.run([
        test_formatting.test_autofit,
        test_formatting.test_handwriting_is_reproducible,
        test_formatting.test_handwriting_varies_letters,
        test_formatting.test_handwriting_stays_on_the_paper,
    ])
    raise SystemExit(_harness.report())
