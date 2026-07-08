# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — freeze the OFT backend (FastAPI + qhfi engine) into a onedir app.

Entry point is run_desktop.py, which starts uvicorn (serving the API + the bundled SPA) and opens
a pywebview window. Build from the backend venv (it has FastAPI + editable qhfi + all data deps):

    cd "C:\\Project\\Open Financial Terminal\\backend"
    .\\.venv\\Scripts\\pyinstaller.exe oft-backend.spec --noconfirm

Debug build (console window + live tracebacks):   set OFT_CONSOLE=1   before the command.
Release build (windowed, logs to %LOCALAPPDATA%\\OpenFinancialTerminal\\logs): leave it unset.

Output: dist/oft-backend/  (a folder; oft-backend.exe is the entry). The Tauri shell embeds this
folder as a resource and spawns oft-backend.exe --server-only; the PyWebView path runs it directly.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPECDIR = Path(SPECPATH).resolve()                       # .../Open Financial Terminal/backend
PROJECT = SPECDIR.parent                                 # .../Open Financial Terminal
QHFI = PROJECT.parent / "quant-hedge-fund-incubator"     # sibling engine repo

datas, binaries, hiddenimports = [], [], []


def add_all(pkg: str) -> None:
    """collect_all (data + binaries + submodules), tolerating absent optional packages."""
    try:
        d, b, h = collect_all(pkg)
        datas.extend(d)
        binaries.extend(b)
        hiddenimports.extend(h)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_all skip {pkg}: {exc}")


def add_submods(pkg: str) -> None:
    try:
        hiddenimports.extend(collect_submodules(pkg))
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_submodules skip {pkg}: {exc}")


def add_data(pkg: str) -> None:
    try:
        datas.extend(collect_data_files(pkg))
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_data_files skip {pkg}: {exc}")


# Packages with lazy/per-plugin submodules or bundled data files that static analysis misses.
for pkg in ("ccxt", "pyarrow", "curl_cffi", "langchain", "langchain_core",
            "langchain_openai", "langgraph", "langsmith", "mcp", "tiktoken_ext"):
    add_all(pkg)

# First-party + dynamically-imported (registry/strategy library, scheduler, server plugins).
for pkg in ("qhfi", "app", "apscheduler", "alpaca", "yfinance", "exchange_calendars"):
    add_submods(pkg)

for pkg in ("exchange_calendars", "certifi"):
    add_data(pkg)

# uvicorn resolves its loop/protocol implementations by string at runtime.
hiddenimports += [
    "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "tiktoken_ext.openai_public",
    "app.main",
]

# Read-only resources the frozen app needs at runtime (see run_desktop.py / app/main.py resolvers).
datas += [
    (str(PROJECT / "frontend" / "dist"), "frontend_dist"),   # served SPA  → _MEIPASS/frontend_dist
    (str(QHFI / "config"), "qhfi_config"),                   # settings.yaml + instruments
    (str(PROJECT / "packaging" / "oft.ico"), "."),           # native-window icon (run_desktop._window_icon)
]

block_cipher = None

a = Analysis(
    ["run_desktop.py"],
    pathex=[str(SPECDIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["rthook_env.py"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],  # matplotlib only needs Agg
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="oft-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=bool(os.environ.get("OFT_CONSOLE")),   # set OFT_CONSOLE=1 for a debuggable build
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "packaging" / "oft.ico"),   # exe + WebView2 window + taskbar icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="oft-backend",
)
