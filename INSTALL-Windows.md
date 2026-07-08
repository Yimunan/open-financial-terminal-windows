# Open Financial Terminal — Windows install

A native Windows desktop build (PyWebView/WebView2 shell over the FastAPI backend + qhfi engine).

## Requirements
- **Windows 10 or 11, 64-bit (x64)** — no 32-bit or ARM64 build.
- **Microsoft Edge WebView2 Runtime** — preinstalled on Windows 11 and most updated Windows 10
  machines. If the app starts but no window appears, install the
  [Evergreen runtime](https://developer.microsoft.com/microsoft-edge/webview2/) and relaunch.
- ~2 GB free disk (the app plus a market-data lake that grows with use).

## Install
1. Download `OpenFinancialTerminal-Setup.exe` from the release. If Edge flags the download, choose
   **… → Keep → Keep anyway**.
2. Run the installer and pick a mode:
   - **All users** (UAC prompt) → installs to `C:\Program Files\Open Financial Terminal`.
   - **Just me** (no admin needed) → installs to `%LOCALAPPDATA%\Programs\Open Financial Terminal`.
     Command-line equivalent: `OpenFinancialTerminal-Setup.exe /CURRENTUSER`.
3. Launch **Open Financial Terminal** from the Start menu (optional desktop icon in the installer).

## First launch — get past SmartScreen
The installer is **not code-signed**, so on first run Windows shows **"Windows protected your PC"**
(Microsoft Defender SmartScreen). That's expected for an open-source app distributed outside the
Microsoft Store:

1. Click **More info**.
2. Click **Run anyway**. (You only do this once.)

> **First launch takes a bit longer** — the app seeds its data directory, kicks off a one-time
> background bootstrap of a baseline market-data lake (Dow 30 + crypto majors, 3 years), and Windows
> Defender scans the bundle once. After that it opens quickly, and charts fill in as data lands.

## Using it
- The app opens its own window — no browser, no login.
- **Charts, screener, backtests, and SEC filings work out of the box** on live public data
  (Yahoo Finance, ccxt/Kraken, SEC EDGAR).
- **AI assistant / NL screener:** open **Settings** (Ctrl+K → Settings) and enter your own
  OpenAI-compatible LLM endpoint + API key (e.g. DeepSeek: base URL `https://api.deepseek.com/v1`,
  model `deepseek-v4-flash`) — or a local server (e.g. Ollama at `http://localhost:11434/v1`), no
  key needed. No key ships with the app.

## Where your data lives / uninstall

| Location | Contents | On uninstall |
|---|---|---|
| `{app}` (Program Files or `%LOCALAPPDATA%\Programs\…`) | the application itself | removed |
| `%APPDATA%\OpenFinancialTerminal` | settings DB, qhfi config + registry, market-data lake | **left behind** |
| `%LOCALAPPDATA%\OpenFinancialTerminal` | logs, per-install encryption key (`secret.key`) | **left behind** |

Uninstall via **Settings → Apps → Installed apps → Open Financial Terminal**. For a full wipe,
also delete the two state folders above — this discards saved settings, portfolios, and encrypted
API keys.

## Notes
- Original terminal + qhfi engine by the upstream authors (MIT-licensed). This build vendors the
  complete engine including its `qhfi.data` / `qhfi.models` packages.
- Macro (FRED) indicators require reaching `fred.stlouisfed.org`; where that host is blocked those
  cards stay empty (rates fall back to Yahoo).
