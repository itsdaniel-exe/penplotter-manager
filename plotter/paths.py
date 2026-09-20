"""Where things live, whether this is running from the source tree or from the
packaged Windows app.

Two different questions, which are the same directory during development and
very different once PyInstaller has bundled everything:

* `resource_dir()` - read-only files shipped *with* the program (the stroke
  fonts, the web UI). In a bundle these are unpacked to a temp folder.
* `data_dir()`     - files the program *writes* (jobs, uploads, diagnostics).
  Next to an installed .exe that may be read-only, or wiped on every launch,
  so per-user AppData is the only sane home for them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Pen Plotter Console"


def is_frozen() -> bool:
    """True when running as the packaged app rather than from source."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    if is_frozen():
        # PyInstaller unpacks bundled data here; falls back to the exe's own
        # folder for a one-folder build.
        return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        path = root / "PenPlotterConsole"
    else:
        path = Path(__file__).resolve().parent.parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def fonts_dir() -> Path:
    return resource_dir() / "fonts"


def web_dir() -> Path:
    return resource_dir() / "web"


def jobs_dir() -> Path:
    path = data_dir() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path
