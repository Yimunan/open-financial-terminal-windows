# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — freeze the qhfi Typer CLI into a standalone qhfi.exe (onedir).

Built with the OFT backend venv (it has qhfi editable + all data deps + PyInstaller already):

    cd "C:\\Project\\quant-hedge-fund-incubator"
    "C:\\Project\\Open Financial Terminal\\backend\\.venv\\Scripts\\pyinstaller.exe" qhfi-cli.spec --noconfirm

Output: dist/qhfi/qhfi.exe . Run it from a directory that contains config/settings.yaml (qhfi reads
that path relative to the CWD — see qhfi.core.config). Note: several CLI commands (data pull /
backtest run / paper) are still NotImplementedError stubs upstream; ownership, mm and research
sector are the implemented ones.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# Shared app icon lives in the sibling OFT repo; only set it if present (keeps the build portable).
_OFT_ICON = Path(SPECPATH).resolve().parent / "Open Financial Terminal" / "packaging" / "oft.ico"
ICON = str(_OFT_ICON) if _OFT_ICON.is_file() else None

datas, binaries, hiddenimports = [], [], []


def add_all(pkg: str) -> None:
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


for pkg in ("ccxt", "pyarrow", "curl_cffi"):
    add_all(pkg)
for pkg in ("qhfi", "apscheduler", "alpaca", "yfinance", "exchange_calendars"):
    add_submods(pkg)
for pkg in ("exchange_calendars", "certifi"):
    datas.extend(collect_data_files(pkg))

a = Analysis(
    ["packaging/qhfi_entry.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="qhfi",
    debug=False,
    strip=False,
    upx=False,
    console=True,                 # a CLI — always keep the console
    disable_windowed_traceback=False,
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="qhfi")
