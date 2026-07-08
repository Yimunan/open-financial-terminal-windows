# Open Financial Terminal — macOS install

A native macOS desktop build (PyWebView shell over the FastAPI backend + qhfi engine).

## Requirements
- **macOS 12 (Monterey) or later**
- **Apple Silicon (M1/M2/M3/…)** — this build is `arm64` only; it will **not** run on Intel Macs.

## Install
1. Download `OpenFinancialTerminal.dmg` from the release.
2. Double-click it, then **drag "Open Financial Terminal" onto the Applications folder**.
3. Eject the disk image.

## First launch — get past Gatekeeper
This app is **ad-hoc signed, not notarized by Apple**, so on first open macOS will warn that it
"cannot be opened because Apple cannot check it for malicious software." That's expected for an
open-source app distributed outside the App Store. Pick **one**:

**Easiest (recommended):**
1. In Finder, open the **Applications** folder.
2. **Right-click** (or Control-click) **"Open Financial Terminal" → Open**.
3. In the dialog, click **Open** again. (You only do this once.)

**If macOS still blocks it (macOS 15 Sequoia):**
1. Try to open it once (it gets blocked).
2. Open **System Settings → Privacy & Security**, scroll down, and click **"Open Anyway"**.

**Terminal one-liner (equivalent):**
```bash
xattr -cr "/Applications/Open Financial Terminal.app" && open "/Applications/Open Financial Terminal.app"
```

> **First launch takes ~20–30 seconds** (the app unpacks a self-contained runtime once). After that it opens quickly.

## Using it
- The app opens its own window — no browser, no login.
- **Charts, screener, backtests, and SEC filings work out of the box** on live public data
  (Yahoo Finance, ccxt/Kraken, SEC EDGAR).
- **AI assistant / NL screener:** open **Settings** (⌘K → Settings) and enter your own
  OpenAI-compatible LLM endpoint + API key (e.g. DeepSeek: base URL `https://api.deepseek.com/v1`,
  model `deepseek-v4-flash`). No key ships with the app.
- App data (cache, settings, keys) is stored under `~/OpenFinancialTerminal/`.

## Notes
- Original terminal + qhfi engine by the upstream authors (MIT-licensed). The `qhfi` data/model
  layer in this build is an independent reconstruction of the packages omitted from the public
  release; some advanced quant features may differ from the original.
- Macro (FRED) indicators require reaching `fred.stlouisfed.org`; where that host is blocked those
  cards stay empty (rates fall back to Yahoo).
