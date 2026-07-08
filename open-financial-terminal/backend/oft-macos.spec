# -*- mode: python ; coding: utf-8 -*-
"""macOS PyInstaller spec — freeze OFT into a native .app bundle.
Build:  cd backend && ~/oft-deploy/.venv-desktop/bin/pyinstaller oft-macos.spec --noconfirm --distpath dist-mac
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPECDIR = Path(SPECPATH).resolve()
PROJECT = SPECDIR.parent
QHFI = PROJECT.parent / "quant-hedge-fund-incubator"

datas, binaries, hiddenimports = [], [], []


def add_all(pkg):
    try:
        d, b, h = collect_all(pkg)
        datas.extend(d); binaries.extend(b); hiddenimports.extend(h)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_all skip {pkg}: {exc}")


def add_submods(pkg):
    try:
        hiddenimports.extend(collect_submodules(pkg))
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_submodules skip {pkg}: {exc}")


def add_data(pkg):
    try:
        datas.extend(collect_data_files(pkg))
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_data_files skip {pkg}: {exc}")


# Lazy/plugin-heavy deps static analysis misses (+ the native webview stack).
for pkg in ("ccxt", "pyarrow", "curl_cffi", "langchain", "langchain_core",
            "langchain_openai", "langgraph", "langsmith", "mcp", "tiktoken_ext",
            "webview"):
    add_all(pkg)

# First-party + dynamically-imported submodules.
for pkg in ("qhfi", "app", "apscheduler", "alpaca", "yfinance", "exchange_calendars",
            "objc", "Foundation", "AppKit", "WebKit", "Quartz", "Cocoa"):
    add_submods(pkg)

for pkg in ("exchange_calendars", "certifi"):
    add_data(pkg)

hiddenimports += [
    "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "tiktoken_ext.openai_public",
    "app.main",
    "webview.platforms.cocoa",
]

datas += [
    (str(PROJECT / "frontend" / "dist"), "frontend_dist"),   # served SPA → _MEIPASS/frontend_dist
    (str(QHFI / "config"), "qhfi_config"),                   # settings.yaml + instruments
]

a = Analysis(
    ["run_desktop.py"],
    pathex=[str(SPECDIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=["rthook_env.py"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="oft-backend",
    debug=False,
    strip=False, upx=False,
    console=False,            # windowed GUI app
    argv_emulation=False,
    target_arch=None,         # native (arm64)
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="oft-backend")

app = BUNDLE(
    coll,
    name="Open Financial Terminal.app",
    icon=str(PROJECT / "packaging" / "oft.icns"),
    bundle_identifier="com.openfinancialterminal.desktop",
    version="2.0.0",
    info_plist={
        "CFBundleName": "Open Financial Terminal",
        "CFBundleDisplayName": "Open Financial Terminal",
        "CFBundleShortVersionString": "2.0.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.finance",
        # WKWebView loads http://127.0.0.1:<port> — allow local networking under ATS.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
