"""PyInstaller entry shim for the standalone qhfi.exe CLI.

PyInstaller freezes a .py file, not a console-script entry point, so this thin wrapper invokes the
Typer app declared at qhfi.cli:app (the same callable the `qhfi` console script targets).
"""

from qhfi.cli import app

if __name__ == "__main__":
    app()
