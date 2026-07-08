# Open Financial Terminal — macOS

A native macOS build of **Open Financial Terminal**: a Bloomberg-style, dockable-widget financial
workspace — live charts, a factor screener, backtesting, portfolio & risk, paper trading, SEC
filings, macro/rates, and an LLM assistant — running as a real Mac app, no browser and no login.

<p align="center">
  <img src="docs/screenshots/hero.png" alt="Open Financial Terminal on macOS" width="900">
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/macOS-12%2B-black?logo=apple">
  <img alt="arch" src="https://img.shields.io/badge/Apple%20Silicon-arm64-0a7">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-blue">
  <a href="https://github.com/Yimunan/open-financial-terminal-mac-os/releases/latest">
    <img alt="release" src="https://img.shields.io/github/v/release/Yimunan/open-financial-terminal-mac-os?display_name=tag"></a>
</p>

---

## ⬇️ Install

1. Download **`OpenFinancialTerminal.dmg`** from the
   [latest release](https://github.com/Yimunan/open-financial-terminal-mac-os/releases/latest).
2. Open it and **drag "Open Financial Terminal" onto the Applications folder**.
3. First launch — because the app isn't Apple-notarized, macOS will warn. **Right-click the app →
   Open → Open** (once). If it's still blocked on macOS 15: **System Settings → Privacy & Security →
   Open Anyway**. Equivalent one-liner:
   ```bash
   xattr -cr "/Applications/Open Financial Terminal.app" && open "/Applications/Open Financial Terminal.app"
   ```

> First launch takes ~20–30s (it unpacks a self-contained runtime once), then it's fast.
> Full details in [INSTALL-macOS.md](INSTALL-macOS.md).

**Requirements:** macOS 12+ on **Apple Silicon (M1/M2/M3/…)**. This build is `arm64`-only.

---

## What's inside

The workspace is a grid of draggable, resizable, tabbed widgets (open more with **⌘K**). Widgets can
be linked on a color channel to share the active symbol; every value is labeled **LIVE / DELAYED /
EOD** so you always know its freshness.

| | |
|---|---|
| **Market data & charts** | Watchlist, Market Board, Quote, Profile, Chart & Chart Studio (candles / Heikin-Ashi / area, indicator overlays, 1m–1d), Order Book & Time-&-Sales (crypto L2). |
| **Screening & factors** | Natural-language Screener, Factor Performance monitor, Factor library — momentum, low-vol, reversal, value (E/P, B/P), quality (ROE, gross margin), alphas, composites. |
| **Backtesting & strategy** | Cross-sectional factor backtests with transaction costs + PSR / Deflated Sharpe, a single-instrument Lab with parameter sweeps, strategy & model libraries. |
| **Portfolio & risk** | Named holdings books, P&L, correlation, Barra factor risk, return & Brinson attribution. |
| **Execution** | Paper trading (local simulator or Alpaca paper), scheduled Algo Trading, Market-Making backtests. |
| **News & filings** | News with LLM sentiment, topic subscriptions, **Public Filings (SEC/EDGAR)**, New Listings. |
| **Macro & FICC** | FRED macro series + the Treasury yield curve; Treasury futures, G10 spot FX, commodity futures. |
| **Research & agents** | Streaming symbol-aware Assistant, autonomous Research Loop, a visual Agent Workflow compiled to LangGraph, Sandbox. |

---

## Data & configuration

- **Live public data out of the box** — equities via Yahoo Finance, crypto via ccxt (Kraken), SEC
  filings via EDGAR, Treasury curve via public rate series. No keys required for these.
- **AI assistant / NL screener** — open **⌘K → Settings** and add your own OpenAI-compatible LLM
  endpoint + API key. **No key ships in the app.** Example (DeepSeek): base URL
  `https://api.deepseek.com/v1`, model `deepseek-v4-flash`.
- App state (cache, settings, encrypted keys) lives under `~/OpenFinancialTerminal/`. Each install
  generates its own encryption key on first run.

---

## Build from source

The `.app`/DMG are produced by [`build/build-macos.sh`](build/build-macos.sh) (PyInstaller freeze of
the FastAPI backend + qhfi engine + the built SPA, wrapped in a `.app` with a PyWebView window). See
[build/README.md](build/README.md) — **including the important qhfi note below.**

### Why a separate macOS repo (the qhfi gitignore fix)

The upstream terminal imports the `qhfi.data` and `qhfi.models` packages, but the public
[quant-hedge-fund-incubator](https://github.com/Yimunan/quant-hedge-fund-incubator) repo is
**missing** them — so a plain clone won't even import. Root cause: its `.gitignore` lists

```
data/
models/
```

for the regenerable data lake, and those patterns **also match the source packages**
`src/qhfi/data/` and `src/qhfi/models/`, so git silently dropped them on push
(`git check-ignore src/qhfi/data/base.py` → `data/`). The one-line fix is to anchor the rules to the
repo root (`/data/`, `/models/`) and re-commit the packages. This macOS build ships with a working
data/model layer already included, so the DMG runs regardless.

---

## Notes

- **Apple Silicon only** — no Intel/`x86_64` build.
- **Not notarized** — expect the Gatekeeper prompt on first open (see Install). Notarization needs an
  Apple Developer account.
- The `qhfi` data/model layer in this build is an independent reconstruction of the packages omitted
  from the public release; some advanced quant behaviors may differ from the original.
- Macro (FRED) cards require reaching `fred.stlouisfed.org`; where that host is blocked they stay
  empty (the Treasury curve falls back to Yahoo tickers).

## Credits & license

Open Financial Terminal and the qhfi engine are **MIT-licensed** by their authors
([open-financial-terminal](https://github.com/Yimunan/open-financial-terminal),
[quant-hedge-fund-incubator](https://github.com/Yimunan/quant-hedge-fund-incubator)). This repo adds
the macOS packaging + build tooling under the same license — see [LICENSE](LICENSE).

*Not investment advice. Data is provided by third parties for informational use only.*
