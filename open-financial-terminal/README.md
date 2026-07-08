# Open Financial Terminal

A financial terminal with a dockable widget workspace, built over the
[qhfi](https://github.com/<github-username>/quant-hedge-fund-incubator) quant engine and a local
OpenAI-compatible LLM endpoint. The frontend is a React/TypeScript single-page app; the backend is a
FastAPI service that imports qhfi as a library. It runs locally and is MIT licensed.

## Contents

- [Overview](#overview)
- [Modules](#modules)
- [Data sources](#data-sources)
- [Requirements](#requirements)
- [Quickstart](#quickstart)
- [Desktop app](#desktop-app)
- [Configuration](#configuration)
- [Paper trading](#paper-trading)
- [Keyboard](#keyboard)
- [Backend API](#backend-api)
- [MCP (Model Context Protocol)](#mcp-model-context-protocol)
- [Project layout](#project-layout)
- [Architecture](#architecture)

## Overview

- **Workspace** — each tool is a widget in a draggable, resizable, tabbed grid (Dockview). Layouts
  are saved as named workspaces in SQLite, and a workspace can be saved as a reusable template.
- **Channel linking** — widgets are assigned to a color channel (red/blue/green) and share an active
  symbol. Selecting a symbol on one channel updates every other widget on the same channel; widgets
  with no channel are independent.
- **Command bar (Ctrl+K)** — search tickers, open widgets, and switch theme. Free text is sent to the
  local LLM, which maps it onto the qhfi factor screener (e.g. "low-vol names in dow30" configures a
  screener widget).
- **Data labeling** — each widget labels its data as LIVE, DELAYED, or EOD so the freshness of every
  value is visible.
- **LLM integration** — the assistant, news sentiment, the natural-language screener, and the agent
  modules call a local OpenAI-compatible endpoint (default `http://localhost:8001/v1`). The endpoint
  and model can be changed from Settings, including pointing at an online provider.
- **Localization** — the UI ships with multiple languages and light/dark themes, plus a configurable
  candle color scheme and accent color.

## Modules

The workspace is composed of widgets, grouped here by area. Not every module is in the default layout;
open more from the command bar (Ctrl+K).

- **Market data and charts** — Watchlist, Market Board, Quote, Profile (metrics, chart, and
  fundamentals tabs for one symbol), Chart and Chart Studio (candles / Heikin Ashi / area, indicator
  overlays, 1m–1d scrubber), Order Book (crypto L2 depth), Time & Sales (crypto trade prints with a
  configurable large-trade threshold), Map (module wiring map).
- **Screening and factors** — Screener (with natural-language query), Factor Performance (factor
  monitor / scorecard), Factors (factor library).
- **Backtesting and strategy** — Backtest (cross-sectional factor backtests, plus a single-instrument
  "Lab" mode with stop-loss/take-profit and parameter sweeps; transaction costs, PSR / Deflated Sharpe
  metrics, explicit date window with P&L), Strategies (strategy library), Models (model repository).
- **Portfolio and risk** — Portfolio (multiple named holdings books, P&L, correlation), Portfolio
  Builder, Risk Attribution (Barra factor risk, realized return attribution, Brinson sector
  attribution).
- **Execution** — Paper Trading (local simulator or Alpaca paper), Algo Trading (scheduled strategy
  runner), Market Making (Avellaneda–Stoikov / inventory-quoting backtests).
- **News and filings** — News (LLM sentiment, configurable sources and ranking), Topic News
  (keyword subscriptions), Public Filings (SEC/EDGAR), New Listings.
- **Macro and FICC** — Macro (FRED series and the Treasury yield curve), FICC (Treasury futures, G10
  spot FX, and commodity futures, EOD quotes). The former standalone Rates view is merged into these.
- **Research and agents** — Assistant (streaming, symbol-aware chat that can also open and configure
  widgets), Research Loop (an autonomous factor-research loop), Agent Workflow (a visual node graph
  compiled to a LangGraph state machine, with an LLM copilot and an agentic edit→run→fix mode),
  Committees (proxied to a separate crewAI service), Sandbox.

See [docs/factors-strategies-models.md](docs/factors-strategies-models.md) and
[docs/algo-trading.md](docs/algo-trading.md) for the quant and execution modules in more depth.

## Data sources

- **Equities** — daily bars from yfinance; optional real-time quotes from Alpaca (IEX free feed or SIP
  paid feed). Fundamentals are point-in-time from qhfi.
- **Crypto** — historical bars and real-time order book / trades / ticker from a ccxt exchange
  (default `kraken`; `binance.com` returns HTTP 451 from US/geo-restricted IPs). Real-time streams use
  ccxt.pro websockets.
- **Rates and macro** — Treasury curve and US macro indicators from FRED.
- **Filings** — SEC/EDGAR filings feed and new-listings scan (set a `SEC_USER_AGENT`).
- **News** — yfinance, Yahoo RSS, and Google News RSS built-ins, plus user-added custom RSS feeds, with
  a configurable ranking formula (recency / source / relevance / sentiment / match weights).

Market data is read from and cached into the qhfi parquet lake (the sibling repo's `data/lake/`).
A background refresh loop keeps the lake current for active symbols on per-job cadences (daily bars,
news, rates, macro, filings); equity-bar refresh can be limited to US market hours. Cadences are set
in Settings → Data Refresh and persist in SQLite.

## Requirements

- Windows (the setup/dev scripts are PowerShell; the stack itself is cross-platform)
- Python 3.11+, Node 18+
- The [qhfi](https://github.com/<github-username>/quant-hedge-fund-incubator) engine cloned as a
  sibling directory next to this repo (so the path resolves to `../quant-hedge-fund-incubator`).
  `scripts/setup.ps1` installs it editable (`pip install -e ../quant-hedge-fund-incubator`) and exits
  if the sibling is missing.
- A local OpenAI-compatible LLM endpoint on `:8001` (e.g. vLLM) for the assistant, news-sentiment, and
  agent features. The rest of the terminal runs without it.
- Optional: an [Alpaca](https://alpaca.markets) paper account for Alpaca-routed paper trading and
  equity real-time quotes; a running crewAI service for the Investment Committee module.

## Quickstart

```powershell
./scripts/setup.ps1   # one-time: backend venv + qhfi (editable) + npm install
./scripts/dev.ps1     # backend :8050 + frontend :5173 in separate windows
```

Open http://localhost:5173 and press **Ctrl+K**. On a fresh install with an empty lake, the backend
pulls a baseline (Dow 30 + major crypto) in the background so the terminal isn't empty.

The interactive API docs are at http://localhost:8050/docs.

## Desktop app

The terminal also runs as a standalone Windows desktop app: a PyWebView shell over a
PyInstaller-frozen backend, packaged with an Inno Setup installer. In a desktop build the backend
serves the built frontend bundle from the same origin, so the webview points straight at
`http://127.0.0.1:<port>` with no CORS or URL rewriting. State is written to
`%APPDATA%\OpenFinancialTerminal` so the install directory stays read-only.

The built installer is not committed to the repo; build it locally following
[docs/DESKTOP.md](docs/DESKTOP.md).

## Configuration

Backend settings use the `OFT_` prefix and are read from environment variables or `backend/.env`
(see `backend/.env.example`). Paths are resolved relative to `backend/`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OFT_DATA_DIR` | `./data` | Terminal data dir (SQLite-adjacent JSON config, caches). |
| `OFT_UNIVERSE_DIR` | sibling qhfi `config/instruments` | Universe (instrument list) YAMLs. |
| `OFT_DB_PATH` | `./oft.sqlite` | Terminal-owned state (workspaces, watchlists, holdings, alerts). |
| `OFT_CORS_ORIGINS` | `localhost:5173,127.0.0.1:5173` | Allowed dev-frontend origins. |
| `OFT_CRYPTO_EXCHANGE` | `kraken` | Default ccxt exchange for crypto bars + streams. |
| `OFT_LLM_MODEL` | unset | Pin a model id; otherwise resolved from the proxy's `/v1/models`. |
| `OFT_LLM_MODEL_PREFER` | `gemma` | Substring preferred when auto-resolving a served model. |
| `OFT_PAPER_INITIAL_CASH` | `100000` | Starting cash for the local paper simulator. |
| `OFT_ALPACA_API_KEY` / `_SECRET` | unset | Route paper orders + equity real-time to Alpaca. |
| `OFT_ALPACA_PAPER` | `true` | Use Alpaca's paper environment. |
| `OFT_DATA_REFRESH_ENABLED` | `true` | Master switch for background lake refresh. |
| `OFT_DATA_REFRESH_*_S` | varies | Per-job cadences (bars/news/rates/macro/filings, seconds). |
| `OFT_DATA_REFRESH_MARKET_HOURS_ONLY` | `true` | Skip equity-bar refresh outside US market hours. |
| `OFT_SECRET_KEY` | generated | Key for encrypting secrets at rest (see below). |
| `QHFI_*` | — | The qhfi engine's own config (e.g. `QHFI_LLM_BASE_URL`, `QHFI_LLM_MODEL`). |

Most data/LLM/news settings are also editable at runtime from the in-app Settings dialog and persist
as small JSON files in the data dir (`llm_provider.json`, `market_data.json`, `news_sources.json`,
`news_topics.json`, `mcp_servers.json`) overlaid onto the env values — changes apply without a
restart.

**Secrets at rest.** Alpaca and online-LLM API keys are stored encrypted (Fernet) using
`OFT_SECRET_KEY` if set, otherwise an owner-only key file generated in the data dir. Both the key file
and the data dir are gitignored.

## Paper trading

With no Alpaca credentials, orders go to a local in-process simulator seeded with
`OFT_PAPER_INITIAL_CASH`. Setting `OFT_ALPACA_API_KEY` / `OFT_ALPACA_API_SECRET` (or entering them in
Settings → Market Data) routes orders to Alpaca's hosted paper environment instead. No live-trading
path is exposed.

## Keyboard

| Key | Action |
| --- | --- |
| `Ctrl+K` | Command bar (tickers, widgets, commands, NL query) |
| `T` | Ticker search (command bar) |
| `C` | New chart widget |
| `N` | New news widget |

Single-key shortcuts are inactive while typing in an input.

## Backend API

The FastAPI app registers ~33 router modules and serves interactive docs at `/docs`. Most routes are
registered directly under `/api` (for example `/api/ask`, `/api/summarize`, `/api/assistant/tools`,
`/api/workspaces`, `/api/templates`). Several modules namespace their routes under a dedicated prefix:

- `/api/agent` — agent workflow builder
- `/api/mm` — market making
- `/api/lab` — strategy lab
- `/api/factor-monitor` — factor performance
- `/api/research`, `/api/committee`, `/api/sandbox` — research / committee / sandbox
- `/api/paper`, `/api/algo` — execution
- `/api/registry`, `/api/settings` (and `/api/settings/data-refresh`) — registry / settings

WebSocket endpoints stream the realtime market hub (`/api/ws/stream`), the assistant chat
(`/api/ws/chat`), the agent run / coder loops (`/api/agent/run`, `/api/agent/code`), and per-module
agent sockets (e.g. `/api/factor-monitor/agent`). The realtime hub ref-counts ccxt.pro exchange
websockets and coalesces fan-out to ~150 ms.

> Trust model: the backend has no authentication (localhost / CORS only). It is intended for local
> use; do not expose it to an untrusted network.

## MCP (Model Context Protocol)

The terminal speaks MCP in both directions.

**Expose the terminal as an MCP server.** A standalone stdio server in `backend/mcp_server/` exposes
the seven read-only assistant tools (`get_quote`, `get_performance`, `get_fundamentals`, `get_news`,
`screen`, `compare`, `search_symbols`) so agents such as Claude Code, Claude Desktop, or OpenCode can
query market data. It is a thin process that calls the running backend over HTTP, so the backend must
be up first.

```bash
# from backend/, with the venv python; backend must be running on :8050
python -m mcp_server.server                 # speaks stdio
# point it at a non-default backend:
OFT_MCP_BASE_URL=http://localhost:8050 python -m mcp_server.server
```

Register it with Claude Code (use the venv's python so `mcp` is importable):

```bash
claude mcp add oft -- "C:\\Project\\Open Financial Terminal\\backend\\.venv\\Scripts\\python.exe" -m mcp_server.server
```

Or in Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "oft": {
      "command": "C:\\Project\\Open Financial Terminal\\backend\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_server.server"],
      "cwd": "C:\\Project\\Open Financial Terminal\\backend",
      "env": { "OFT_MCP_BASE_URL": "http://localhost:8050" }
    }
  }
}
```

**Consume external MCP servers.** Register MCP servers (Settings → MCP Servers, or by editing
`backend/data/mcp_servers.json`) and their tools join the assistant's plan→fetch→stream loop,
namespaced `mcp:<server>:<tool>`. Discovery is best-effort: a server that is down or misconfigured is
skipped and does not break chat. Each entry is `{name, transport: "stdio"|"http", command, args, env,
url, headers, enabled}` (up to 20 servers).

## Project layout

```
Open Financial Terminal/
├── backend/                 FastAPI service + MCP server + desktop launcher
│   ├── app/
│   │   ├── main.py          app entrypoint: router registration, SPA mount, startup lifespan
│   │   ├── config.py        OFT_ settings + persisted Settings overrides (LLM/market/news/MCP)
│   │   ├── deps.py          singletons: DataManager, LLMClient, broker, store, background runners
│   │   ├── routers/         ~33 HTTP/WebSocket route modules
│   │   ├── services/        adapters: realtime hub, screener, strategy lab, risk, agents, ...
│   │   └── store.py         SQLite state (workspaces, holdings books, watchlists, alerts)
│   ├── mcp_server/          stdio MCP server (7 read-only tools)
│   ├── run_desktop.py       PyWebView launcher (and --server-only for a sidecar)
│   ├── oft-backend.spec     PyInstaller spec
│   └── pyproject.toml
├── frontend/                React + TypeScript + Vite SPA
│   └── src/
│       ├── widgets/         dockable widget components
│       ├── state/           zustand stores (channel linking, workspaces, agent runs, settings)
│       ├── lib/             canvas chart engine, websocket client, i18n
│       └── api/             REST client + types
├── packaging/               Inno Setup installer script
├── scripts/                 setup.ps1, dev.ps1, build_desktop.ps1
└── docs/                    ARCHITECTURE.md, DESKTOP.md, algo-trading.md, factors-strategies-models.md
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). In short: a FastAPI backend that imports qhfi as a
library (it does not reimplement quant logic), a realtime hub that ref-counts ccxt.pro exchange
websockets and coalesces fan-out to ~150 ms, and a React/TypeScript frontend where Dockview owns
layout, zustand owns channel linking, and TanStack Query owns REST state. In a desktop build the
backend additionally serves the built frontend from the same origin.
