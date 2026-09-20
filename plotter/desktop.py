"""Desktop launcher: the console in its own window, no browser, no terminal.

The app is still the same local web server - this starts it on a free port,
waits for it to answer, and shows it in a native window using the WebView2
runtime that ships with Windows. Closing the window stops the server.

    .venv\\Scripts\\python -m plotter.desktop
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from .paths import APP_NAME, data_dir

# A second copy would fight the first for the serial port, and Windows allows
# exactly one handle on a COM port. Holding a socket is the simplest lock that
# cannot outlive the process.
_SINGLE_INSTANCE_PORT = 58765
_instance_lock: socket.socket | None = None


def _claim_single_instance() -> bool:
    global _instance_lock
    try:
        lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lock.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        lock.listen(1)
    except OSError:
        return False
    _instance_lock = lock
    return True


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))
                return probe.getsockname()[1]
        except OSError:
            continue
    raise RuntimeError("No free port to run the console on.")


def _setup_logging() -> None:
    """A windowed build has no console, so the log has to go to a file."""
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "app.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _wait_until_up(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _message_box(text: str, title: str = APP_NAME) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001 - nothing better to do if even this fails
        print(text, file=sys.stderr)


def main() -> int:
    import uvicorn
    import webview

    from . import __version__
    from .server import app, install_log_capture

    if not _claim_single_instance():
        _message_box(
            f"{APP_NAME} is already running.\n\n"
            "Look for its window, or its icon in the taskbar."
        )
        return 0

    _setup_logging()
    install_log_capture()
    logging.getLogger(__name__).info("starting %s %s", APP_NAME, __version__)

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="console-server", daemon=True)
    thread.start()

    if not _wait_until_up(port):
        _message_box(
            "The console could not start.\n\n"
            f"Its log is in:\n{data_dir() / 'logs' / 'app.log'}"
        )
        return 1

    webview.create_window(
        f"{APP_NAME}  {__version__}",
        f"http://127.0.0.1:{port}",
        width=1500,
        height=950,
        min_size=(1024, 720),
        text_select=True,
    )
    # Blocks until the window is closed.
    webview.start()

    # Let the machine finish what it was told to do, then stop the server.
    server.should_exit = True
    thread.join(timeout=45)
    logging.getLogger(__name__).info("closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
