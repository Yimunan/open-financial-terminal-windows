"""Desktop entrypoint for Open Financial Terminal.

Starts the FastAPI backend (which also serves the built frontend — see app/main.py) and, by
default, opens it in a native webview window via pywebview. The webview points straight at
http://127.0.0.1:<port>, so the SPA's relative `/api` calls and `location.host` WebSockets work
with no CORS and no URL rewriting.

Modes
-----
  python run_desktop.py                 # start server + open the native window (personal launcher)
  python run_desktop.py --server-only   # start server only, print the URL, block (Tauri sidecar)

When frozen (PyInstaller), writable state is relocated under %APPDATA%/OpenFinancialTerminal so a
read-only install location (Program Files) never gets written to. This MUST happen before importing
app.main, because app.config reads the OFT_* env vars at import time (lru_cached settings).
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
MEIPASS = Path(getattr(sys, "_MEIPASS", BACKEND_DIR))

# Make `import app.main` work when launched directly (dev) — frozen builds resolve via the bundle.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _window_icon() -> str | None:
    """Path to the .ico for the native window + taskbar.

    Dev runs use the repo copy (packaging/oft.ico); frozen builds use the copy bundled by
    oft-backend.spec (→ _MEIPASS/oft.ico). The winforms backend loads it via System.Drawing.Icon,
    which needs a real .ico. If neither path exists we return None and pywebview falls back to the
    icon embedded in the exe (spec `icon=`), so the window stays branded either way.
    """
    for candidate in (MEIPASS / "oft.ico", BACKEND_DIR.parent / "packaging" / "oft.ico"):
        if candidate.is_file():
            return str(candidate)
    return None


def _ensure_std_streams() -> None:
    """Guarantee usable sys.stdout/sys.stderr for windowed launches.

    Under pythonw.exe and PyInstaller --windowed there is no console, so sys.stdout/stderr are
    None. uvicorn (and other libs) configure logging against ext://sys.stderr and crash on the
    first log write. Point both at a rotating-ish app log file so the GUI process stays alive and
    we still get diagnostics. No-op when a real console is attached (dev runs keep their terminal).
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "OpenFinancialTerminal" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = open(log_dir / "desktop.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = stream
    sys.stderr = stream


def _relocate_state_when_frozen() -> None:
    """Set up a per-user working directory + writable state for installed (frozen) builds.

    A frozen exe can be launched from anywhere (and may live under read-only Program Files), but
    qhfi reads ``config/settings.yaml`` and writes ``./registry.sqlite`` / ``./data`` RELATIVE TO
    THE CWD (see qhfi.core.config). So we:
      1. create a writable per-user work dir under %APPDATA%,
      2. seed it once with the bundled read-only ``config/`` (settings.yaml + instruments),
      3. chdir into it, so every qhfi relative path resolves to a populated, writable location,
      4. pin OFT's own state to absolute %APPDATA% paths via the OFT_* env (config.py reads these).

    Only fills vars the user hasn't already set, so a power user can still override OFT_*. No-op for
    dev runs (FROZEN is False) — those keep the repo-relative defaults and the editable qhfi tree.
    """
    if not FROZEN:
        return
    appdata = Path(os.environ.get("APPDATA", Path.home())) / "OpenFinancialTerminal"
    work = appdata / "work"
    work.mkdir(parents=True, exist_ok=True)

    # Seed the writable config/ from the bundle on first run (don't clobber user edits afterward).
    bundled_config = MEIPASS / "qhfi_config"
    dest_config = work / "config"
    if bundled_config.is_dir() and not dest_config.exists():
        shutil.copytree(bundled_config, dest_config)
    os.chdir(work)  # qhfi's Path("config/settings.yaml") + ./registry.sqlite now resolve here

    defaults = {
        "OFT_DATA_DIR": str(appdata / "data"),
        "OFT_DB_PATH": str(appdata / "oft.sqlite"),
        # The qhfi parquet lake is large and NOT frozen into the bundle; it lives per-user and is
        # populated by the Data Refresh runner / qhfi `data pull` on first run.
        "OFT_QHFI_LAKE_DIR": str(appdata / "lake"),
        # Universe configs are small; point at the seeded writable copy for consistency.
        "OFT_UNIVERSE_DIR": str(dest_config / "instruments"),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _pick_port(preferred: int = 8050) -> int:
    """Return the preferred port if free, otherwise an OS-assigned ephemeral port.

    The probe deliberately does NOT set SO_REUSEADDR: on Windows that option lets a probe bind a
    port another process is actively listening on (false "free"), which would then collide when
    uvicorn binds for real. Plain bind() fails on an in-use port, so an occupied 8050 (e.g. the
    dev backend already running) correctly falls through to an OS-assigned ephemeral port.
    """
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port available")


def _wait_for_health(url: str, timeout: float = 60.0) -> bool:
    """Poll /api/health until it answers 200 or the timeout elapses."""
    deadline = time.monotonic() + timeout
    health = f"{url}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=2) as resp:  # noqa: S310 - localhost only
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - server still starting up
            time.sleep(0.25)
    return False


class _WindowBridge:
    """JS→Python bridge exposed to the SPA as ``window.pywebview.api``.

    Backs the per-widget "open in new window" (⧉) control. Inside pywebview the page runs in a
    WKWebView (macOS) / EdgeChromium (Windows) whose UI delegate blocks *script-initiated*
    ``window.open()`` — so Dockview's built-in popout silently no-ops in the desktop app (it works
    only in a real browser). This bridge is the desktop equivalent: the frontend hands us the target
    widget's type + params as a ready-made ``/?solo=…`` path, and we spawn a genuine native window
    that renders just that one widget (see frontend SoloWorkspace).

    ``webview.create_window`` is safe to call from this js_api callback thread: pywebview marshals
    child-window creation onto the GUI run loop (cocoa uses ``AppHelper.callAfter``).
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def open_panel_window(self, payload: dict | None = None) -> bool:
        import webview  # noqa: PLC0415 - window mode only

        payload = payload or {}
        # `path` is a frontend-built "/?solo=<type>&p=<json>&t=<title>" (see lib/popout.ts). Only ever
        # a same-origin relative path; ignore anything that isn't so a malformed value can't retarget
        # the window off-origin.
        path = str(payload.get("path") or "/")
        if not path.startswith("/"):
            path = "/"

        def _clamp(value, default: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int(value)))
            except (TypeError, ValueError):
                return default

        webview.create_window(
            str(payload.get("title") or "Open Financial Terminal"),
            f"{self._base_url}{path}",
            width=_clamp(payload.get("width"), 900, 360, 2400),
            height=_clamp(payload.get("height"), 640, 240, 1600),
            min_size=(360, 240),
        )
        return True


def _serve(port: int):
    """Build the uvicorn server bound to localhost:<port>. Import app lazily (post-relocate)."""
    import uvicorn

    from app.main import app  # noqa: PLC0415 - must import AFTER _relocate_state_when_frozen()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", access_log=False)
    return uvicorn.Server(config)


def main() -> int:
    _ensure_std_streams()
    _relocate_state_when_frozen()
    server_only = "--server-only" in sys.argv

    port = _pick_port(8050)
    url = f"http://127.0.0.1:{port}"
    server = _serve(port)

    if server_only:
        # The shell (e.g. Tauri) reads this line to learn the URL, then polls health itself.
        print(f"OFT_URL={url}", flush=True)
        server.run()  # blocks until killed
        return 0

    # Window mode: run the server on a daemon thread, gate on health, then open the webview.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_for_health(url):
        print("backend did not become healthy in time", file=sys.stderr)
        return 1

    import webview  # noqa: PLC0415 - only needed in window mode (the `desktop` extra)

    webview.create_window(
        "Open Financial Terminal",
        url,
        js_api=_WindowBridge(url),  # backs the per-widget "open in new window" (⧉) control
        width=1400,
        height=900,
        min_size=(1024, 700),
    )
    webview.start(icon=_window_icon())  # blocks on the GUI loop; returns when the window closes
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
