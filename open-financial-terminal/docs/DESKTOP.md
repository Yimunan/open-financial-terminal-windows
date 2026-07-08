# Desktop builds — Open Financial Terminal + qhfi

This doc covers shipping OFT as a desktop application: a quick **personal launcher** for this
machine, and a **distributable Windows installer**. qhfi has no GUI of its own — OFT *is* its
graphical front end (it imports qhfi as the engine) — so "the qhfi desktop app" is the OFT app,
plus an optional standalone `qhfi.exe` CLI for headless use.

## Architecture: one origin

OFT's SPA talks to the backend with **relative `/api` paths** and **`location.host` WebSockets**
([frontend/src/api/client.ts](../frontend/src/api/client.ts)). So instead of shipping the SPA and
the API as two origins (and fighting CORS), the desktop build makes the **FastAPI backend also
serve the built SPA** ([app/main.py](../backend/app/main.py) mounts `frontend/dist`), and a native
**WebView2** window points straight at `http://127.0.0.1:<port>`. The window's origin *is* the
backend, so every REST call and WebSocket resolves with zero frontend changes and no CORS.

```
oft-backend.exe  (or .venv\python run_desktop.py)
  ├─ picks a free port (8050, else ephemeral — avoids clashing with a running dev backend)
  ├─ uvicorn serves  /api/*  +  /api/ws/*  +  the SPA at /
  └─ pywebview opens a WebView2 window → http://127.0.0.1:<port>
```

The launcher is [backend/run_desktop.py](../backend/run_desktop.py). Run it two ways:

| Command | Behavior |
|---|---|
| `run_desktop.py` | start server + open the native window (the desktop app) |
| `run_desktop.py --server-only` | start server, print `OFT_URL=…`, block (for an external shell) |

## Personal launcher (fastest — uses the existing venv)

No build/freeze needed; reuses `backend/.venv`.

```powershell
cd "C:\Project\Open Financial Terminal\frontend"; npm run build   # once, so frontend/dist exists
# then just double-click:
C:\Project\Open Financial Terminal\OpenFinancialTerminal.bat
```

[OpenFinancialTerminal.bat](../OpenFinancialTerminal.bat) launches `pythonw.exe run_desktop.py`
(no console window). Logs go to `%LOCALAPPDATA%\OpenFinancialTerminal\logs\desktop.log`.

## Distributable installer (no Python/Node on the target machine)

Freezes the backend + qhfi + the SPA into a self-contained onedir app with PyInstaller, then wraps
it in a Windows installer. The frozen app opens its own WebView2 window, so **no Rust/Tauri/Electron
is involved** — WebView2 ships with Windows 11.

```powershell
# release (windowed) build:
pwsh "C:\Project\Open Financial Terminal\scripts\build_desktop.ps1"
#   → backend\dist_desktop\oft-backend\oft-backend.exe   (~560 MB onedir; double-click to run)

# also build the standalone CLI and compile the installer:
pwsh scripts\build_desktop.ps1 -Qhfi -Installer
#   → packaging\dist_installer\OpenFinancialTerminal-Setup-2.0.0.exe
```

[scripts/build_desktop.ps1](../scripts/build_desktop.ps1) runs: `npm run build` →
`pyinstaller oft-backend.spec` → (optional) `pyinstaller qhfi-cli.spec` → (optional) Inno Setup.

- **Debug a freeze:** `build_desktop.ps1 -Console` builds with a console window + live tracebacks
  (sets `OFT_CONSOLE=1`, read by [oft-backend.spec](../backend/oft-backend.spec)). The release build
  is windowed and logs to `desktop.log` instead.
- **Installer prereq:** Inno Setup 6 — `winget install JRSoftware.InnoSetup` (installs user-scope to
  `%LOCALAPPDATA%\Programs`). Script: [packaging/oft-installer.iss](../packaging/oft-installer.iss);
  it defaults to an all-users (admin) install but also supports a no-admin per-user install via the
  wizard or `Setup.exe /CURRENTUSER`.
- **Icon:** a bold black **"OFT"** wordmark (Segoe UI Black, optically kerned) on a white **squircle**
  (superellipse) tile, proportioned by the **golden ratio** — the wordmark spans 1/φ (≈61.8 %) of the
  tile width, leaving golden margins. [packaging/oft.ico](../packaging/oft.ico) — regenerate with
  [packaging/make_icon.py](../packaging/make_icon.py), which renders every size (16–256 px)
  independently (8× supersample for the 16/32 px frames) so the wordmark stays crisp at taskbar size —
  is embedded in the exe, the WebView2 window, and the installer. It also emits PNG masters
  (`oft.png` 256, `oft-512.png`, `oft-1024.png`) and writes the SPA favicons into `frontend/public/`
  (`favicon.ico` + `apple-touch-icon.png`). The vector favicons are hand-maintained and **adaptive**:
  `favicon.svg` (white tile) for dark browser chrome + `favicon-light.svg` (transparent black "OFT")
  for light chrome, switched via `prefers-color-scheme` in [frontend/index.html](../frontend/index.html)
  and shipped via `frontend/dist`.
- **Code signing:** add `-Sign` to authenticode-sign the exe(s) + installer. Configure the cert via
  `OFT_SIGN_THUMBPRINT` (cert store) or `OFT_SIGN_PFX` + `OFT_SIGN_PFX_PASSWORD`. Without a cert it
  skips with a warning. Unsigned binaries trip SmartScreen ("unknown publisher") on other machines.
- **qhfi.exe** is also zipped to `packaging/dist_installer/qhfi-cli-windows-x64.zip` when `-Qhfi`.

### What the freeze bundles & where state goes
- `frontend/dist` → `_internal/frontend_dist` (served SPA; matches the resolver in `app/main.py`).
- qhfi `config/` (settings.yaml + instruments) → `_internal/qhfi_config` (seeded to the work dir on
  first run).
- When **frozen**, `run_desktop.py` relocates writable state so a read-only Program Files install
  works: it `chdir`s to `%APPDATA%\OpenFinancialTerminal\work` (seeded with `config/`, so qhfi's
  CWD-relative `config/settings.yaml` + `./registry.sqlite` resolve), and pins
  `OFT_DATA_DIR` / `OFT_DB_PATH` / `OFT_QHFI_LAKE_DIR` under `%APPDATA%\OpenFinancialTerminal`.

## Standalone qhfi.exe (optional CLI)

[quant-hedge-fund-incubator/qhfi-cli.spec](../../quant-hedge-fund-incubator/qhfi-cli.spec) freezes
the Typer CLI (`qhfi.cli:app`) into `dist_desktop\qhfi\qhfi.exe`. Run it from a directory that
contains `config/settings.yaml` (qhfi reads that path relative to the CWD). Note: several commands
(`data pull`, `backtest run`, `paper`) are still upstream stubs; `ownership`, `mm`, and
`research sector` are the implemented ones — OFT exercises the rest of the engine as a library.

## Gotchas

- **First-run data bootstrap.** The parquet lake is large and *not* frozen, so a fresh install
  starts empty. On first launch the backend auto-pulls a baseline (default **dow30 + crypto_majors**,
  3y) into the lake and seeds a starter watchlist, so charts/screener/metrics work without manual
  setup. It runs in a **background thread** (the window opens immediately; data fills in as it lands)
  and is gated by a `.bootstrapped` sentinel + an empty-lake check, so it never re-runs and never
  touches an already-populated lake (your dev install is untouched). Progress is in `/api/health`
  under `bootstrap` (`idle`/`running`/`done`/`skipped`/`error`) and in `desktop.log`. Implementation:
  [app/services/bootstrap.py](../backend/app/services/bootstrap.py), invoked from the lifespan in
  [app/main.py](../backend/app/main.py). Tune via env: `OFT_BOOTSTRAP_DISABLE=1`,
  `OFT_BOOTSTRAP_UNIVERSES="dow30,crypto_majors"`, `OFT_BOOTSTRAP_YEARS=3`,
  `OFT_BOOTSTRAP_WATCHLIST="AAPL:equity,BTC/USDT:crypto"`. Deep history beyond the baseline still
  fills in via the scheduled Data Refresh; you can also pull more from Settings → Data Refresh.
- **Two instances + Alpaca.** Running the desktop app while the dev backend is up logs
  `alpaca … connection limit exceeded` — Alpaca allows one realtime websocket per account. Harmless;
  only one instance gets the equity tape. Crypto (Kraken) and everything else still work.
- **Port.** 8050 is preferred but auto-falls-back to an ephemeral port if busy (e.g. dev backend
  running), so the desktop app never collides.
- **External LLM services aren't bundled.** Assistant/agent/research need the vLLM proxy (`:8001`)
  or an online provider set in Settings → LLM; the Investment Committee needs the CrewAI service
  (`:8083`). The app runs and degrades gracefully without them.
- **WebView2** is the only OS runtime dependency; it ships with Windows 11 (verified present here).

## Optional: Tauri shell instead of pywebview

Not required — the frozen pywebview app is already a complete windowed installer-ready app, and
OFT needs no native IPC. Choose Tauri only if you specifically want its installer/auto-updater/
code-signing pipeline or a branded window (the toolchain is already proven by `C:\Project\llm_wiki`).
The integration is: scaffold `src-tauri/`, bundle the PyInstaller onedir as a Tauri **resource**,
and in Rust `setup()` spawn `resources/oft-backend/oft-backend.exe --server-only`, read its
`OFT_URL=` line, poll `/api/health`, then build a `WebviewWindow` pointing at that URL. Reuse the
subprocess + resource-path patterns from `llm_wiki/src-tauri/src/{commands/claude_cli.rs,lib.rs}`.

## Files

| File | Role |
|---|---|
| [backend/run_desktop.py](../backend/run_desktop.py) | Desktop entrypoint: port pick, frozen-state relocation, server, health gate, window |
| [backend/app/main.py](../backend/app/main.py) | Serves `frontend/dist` (SPA) alongside the API |
| [backend/oft-backend.spec](../backend/oft-backend.spec) | PyInstaller freeze of the backend + engine + SPA |
| [scripts/build_desktop.ps1](../scripts/build_desktop.ps1) | Build orchestration (frontend → freeze → installer) |
| [packaging/oft-installer.iss](../packaging/oft-installer.iss) | Inno Setup installer script |
| [OpenFinancialTerminal.bat](../OpenFinancialTerminal.bat) | Double-click personal launcher |
| [../quant-hedge-fund-incubator/qhfi-cli.spec](../../quant-hedge-fund-incubator/qhfi-cli.spec) | Standalone `qhfi.exe` freeze |
