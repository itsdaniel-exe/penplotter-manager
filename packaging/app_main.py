"""Entry point for the packaged Windows app.

A thin wrapper so PyInstaller has a plain script to start from, while the real
launcher stays a proper module inside the package.
"""

from __future__ import annotations

import multiprocessing

from plotter.desktop import main

if __name__ == "__main__":
    # Harmless when running from source; required so a frozen build never
    # re-launches the whole app in a child process.
    multiprocessing.freeze_support()
    raise SystemExit(main())
