"""Build the Windows app.

    .venv\\Scripts\\python packaging/build.py

Produces packaging/dist/PenPlotterConsole.exe - one file, no installer, no
Python needed on the machine that runs it. Hand that file to whoever has the
plotter; they double-click it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
DIST = PACKAGING / "dist"
BUILD = PACKAGING / "build"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from plotter import __version__

    if not (PACKAGING / "icon.ico").exists():
        print("icon missing - generating it first")
        subprocess.run([sys.executable, str(PACKAGING / "make_icon.py")], check=True)

    print(f"building Pen Plotter Console {__version__}")
    started = time.time()
    result = subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath", str(DIST),
            "--workpath", str(BUILD),
            str(PACKAGING / "penplotter.spec"),
        ],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print("build FAILED")
        return result.returncode

    exe = DIST / "PenPlotterConsole.exe"
    if not exe.exists():
        print("build finished but the exe is missing")
        return 1

    # Stamp the version into the filename so two downloads never look alike.
    stamped = DIST / f"PenPlotterConsole-{__version__}.exe"
    shutil.copy2(exe, stamped)

    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"\ndone in {time.time() - started:.0f}s")
    print(f"  {exe}  ({size_mb:.0f} MB)")
    print(f"  {stamped}")
    print("\nTest it here first, then attach the stamped copy to a GitHub release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
