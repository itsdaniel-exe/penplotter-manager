"""Tiny check/report harness shared by the test modules.

Deliberately not pytest: the project's requirements.txt is the runtime set,
and these need to be runnable on the machine that drives the plotter without
installing anything extra.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f" - {detail}" if detail else ""))
        FAILURES.append(label)


def run(tests) -> None:
    for test in tests:
        test()
        print()


def report() -> int:
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


class RecordingPort:
    """Minimal pyserial-shaped transport that records what was written."""

    is_open = True

    def __init__(self):
        self.written: list[str] = []

    def write(self, data: bytes) -> None:
        self.written.append(data.decode().strip())

    def readline(self) -> bytes:
        return b"ok\r\n"

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False
