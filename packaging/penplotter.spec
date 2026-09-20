# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the packaged Windows app.

One file, no console window, fonts and web UI bundled inside. Build it with:

    .venv\\Scripts\\python packaging/build.py
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

# pywebview needs its platform backend and pythonnet's runtime DLLs; uvicorn
# imports its loop/protocol implementations by name, so nothing static finds
# them.
webview_datas, webview_binaries, webview_hidden = collect_all("webview")
loader_datas, loader_binaries, loader_hidden = collect_all("clr_loader")

datas = [
    (str(ROOT / "fonts"), "fonts"),
    (str(ROOT / "web"), "web"),
] + webview_datas + loader_datas

binaries = webview_binaries + loader_binaries

hiddenimports = (
    webview_hidden
    + loader_hidden
    + collect_submodules("uvicorn")
    + [
        "clr",
        "webview.platforms.edgechromium",
        "plotter.server",
        "plotter.desktop",
    ]
)

a = Analysis(
    [str(ROOT / "packaging" / "app_main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing here draws with Tk or plots with matplotlib; excluding them keeps
    # the download a sensible size.
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PenPlotterConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # its own window, no terminal behind it
    icon=str(ROOT / "packaging" / "icon.ico"),
)
