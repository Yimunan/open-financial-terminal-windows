# Architecture

## Principle: terminal = interactive layer, qhfi = engine

The terminal never reimplements quant logic. It imports **qhfi** (editable install from the
sibling repo) and calls its public API — data lake, factors, the backtest accounting loop,
risk metrics. The terminal and the quant fund always agree on numbers. v2 keeps v1's verified
backend services and replaces the fixed five-view frontend with a dockable widget workspace
plus a realtime crypto layer.

```
frontend :5173 (Vite, React/TS)
  │  /api REST + /api/ws/chat + /api/ws/stream   (single origin via Vite proxy)
backend :8050 (FastAPI)
  ├── services/* → qhfi (DataManager, factors, BacktestEngine, evaluation, LLMClient)
  ├── services/realtime.py → ccxt.pro exchange websockets (crypto depth/trades/tickers)
  └── :8001 local vLLM proxy (model id resolved from /v1/models)
```

## Backend

**Routers** (`app/routers/`): `health`, `market` (search/universes/bars/quote), `screener`,
`backtest`, `portfolio` (watchlist/holdings/risk), `fundamentals`, `assistant`
(ask/summarize + `/api/ws/chat`), `workspace` (named Dockview layouts), `stream`
(`/api/ws/stream`).

**Carried over from v1 (verified working):**

- `deps.get_llm_model()` — queries the proxy's `/v1/models` once and picks a served id
  (prefers `gemma`, `OFT_LLM_MODEL` overrides). The auto-swap proxy keys on full HF ids, so
  bare tags like `gemma` would be rejected.
- `services/assistant.stream_chat` — direct SSE to the proxy because qhfi's `LLMClient` is
  request/response only.
- `services/factors.py` — the 11-factor catalog (price + value/quality + 6 Alpha101) and
  `trial_keys()`, which the backtest uses as the trial count for the **Deflated Sharpe**.
- `services/backtest._weights_from_scores` — monthly-rebalanced cross-sectional weights
  (long-only top `top_pct`, or dollar-neutral ±0.5 per side) into qhfi's `BacktestEngine`
  with real commission/slippage/financing.

**New in v2:**

- `services/market.fetch_bars_intraday` — intraday OHLCV straight from the provider
  (ccxt for crypto, yfinance for equities) behind a 60s in-memory TTL cache. Deliberately
  never written into the qhfi parquet lake; daily bars keep using `DataManager`'s
  incremental refresh. Daily candles use date strings, intraday uses unix seconds
  (lightweight-charts' two time encodings).
- `services/realtime.py` — the realtime hub (below).
- `store.py` gains a `workspaces` table (name → Dockview layout JSON).

## Realtime hub

One multiplexed websocket per browser tab (`/api/ws/stream`); the client sends
`{op: sub|unsub, topic}` where a topic is `kind:exchange:symbol`
(`book:binance:BTC/USDT`, kinds: `ticker | book | trades`).

- **Ref-counted upstream tasks** — one asyncio task per topic using ccxt.pro's
  `watch_ticker / watch_order_book / watch_trades`, started on the first subscriber,
  cancelled on the last unsub. Exchange clients are shared per exchange id and closed on
  app shutdown (FastAPI lifespan).
- **Server-side coalescing** — watch tasks only update per-topic buffers. A single flusher
  pushes dirty topics every **150ms**: ticker/book snapshots are replaced (latest wins),
  trades are append-buffered and drained, so no prints are lost. The UI renders frames
  directly; no second client-side throttle is needed.
- **Slow-consumer policy** — subscriber queues are bounded (500); a stalled tab drops frames
  instead of back-pressuring the hub.
- New subscribers immediately receive the last snapshot so widgets paint without waiting
  for the next upstream tick.
- `GET /api/stream/stats` shows live topics/subscribers for debugging.

Crypto-only by design: there is no free realtime depth/tape for US equities. The topic scheme
is provider-agnostic so a paid equities feed can slot in behind the same interface.

## Frontend

- **Dockview** owns the grid: drag/dock/tabs/resize and layout JSON. `DockviewWorkspace`
  restores the `default` workspace from the backend on load (or builds a starter layout) and
  autosaves on layout change (1.5s debounce). Widget params (channel, symbol, timeframe,
  chart type, indicators) live in panel params, so they serialize with the layout.
- **Widget registry** (`workspace/widgetRegistry.tsx`) is the single source of truth:
  component map for Dockview, titles, default channels, and the command bar's widget list.
  Adding a widget = one entry + one component file.
- **Channel linking** (`state/linking.ts`, zustand): red/blue/green channels each hold an
  active `{symbol, asset}`. `useWidgetSymbol` resolves a widget's symbol from its channel,
  or from its own panel params when unlinked (`none`). Watchlist clicks, screener row picks
  and Ctrl+K ticker jumps all write to a channel.
- **wsClient** (`lib/wsClient.ts`): singleton socket, client-side ref-counting per topic,
  exponential-backoff reconnect with automatic re-subscribe.
- **Theme**: CSS variable tokens on `<html data-theme>`; Tailwind reads them via
  `rgb(var(--…))`. Charts can't read CSS variables, so chart options are built from
  `getComputedStyle` and charts are recreated on theme flip. `font-variant-numeric:
  tabular-nums` globally (no jitter); true monospace on the book/tape.
- **Data honesty**: every widget shows a badge — LIVE (websocket-fed), DELAYED (intraday
  REST poll), EOD (daily lake).

## Verification numbers (sanity anchors from v1)

Momentum long-only on dow30, 3y: Sharpe ≈ 1.0, PSR ≈ 97%, DSR ≈ 93%. A losing strategy
should show PSR/DSR near zero. If PSR/DSR come back wildly different after touching
`services/factors.py`, check `trial_keys()` first.

## Intentionally not built (seams for v2.x)

Paid equities tape/L2, alerts engine (table exists, no engine), Alpaca paper-trading widget
(`AlpacaPaperBroker.from_env()` is real in qhfi), FRED macro/rates panel, options chains,
EDGAR filings. qhfi stubs (`PaperLoop`, `Registry`, agent bridges) are avoided entirely.
