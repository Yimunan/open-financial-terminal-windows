@echo off
REM Open Financial Terminal — personal desktop launcher (Phase 1).
REM Starts the FastAPI backend (which serves the SPA) and opens it in a native WebView2 window.
REM Console-less via pythonw; `start ""` returns immediately so no stray console lingers.
cd /d "%~dp0backend"
start "" ".venv\Scripts\pythonw.exe" "run_desktop.py"
