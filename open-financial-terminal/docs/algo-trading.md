# Algo Trading — User Manual

The **Algo Trading** widget runs strategies on a schedule and routes their live signals to the
same paper account the **Paper Trading** widget uses. It's the bridge between research
(StrategyLab / Backtest, which tell you what *would* have happened) and live(-ish) paper
execution (what to do *now*).

An **always-on backend runner** drives it: armed algos fire on their cadence even with no
browser open, and survive a server restart. Nothing trades until you **arm** an algo (or click
**Run**); saving an algo never places an order.

---

## Concepts

**Algo** — a saved, schedulable strategy config. Two kinds:

| Kind | What it trades | Signal source |
| --- | --- | --- |
| **Single-symbol** (`template`) | one ticker | a StrategyLab template (SMA/EMA cross, RSI, MACD, Bollinger, Donchian, or a custom strategy). The template's current bar is the live signal: **long / short / flat**. |
| **Cross-sectional** (`xsection`) | a whole universe (dow30, sp500, …) | a factor (momentum, value, quality, low-vol, Alpha101, …) ranked across the universe into a long/short or long-only book — the same engine the Backtest widget uses. |

**Broker** — auto-selected. With no Alpaca keys it's the local **SIM** (in-process simulator,
state in the terminal DB). With Alpaca keys configured (Settings → Market Data) it's **ALPACA**
paper — *real* paper orders on Alpaca's hosted environment. The badge in the toolbar shows which.

**Cycle** — one run of an algo: compute signal → target weights → risk-gate → reconcile against
the current account → submit the difference as orders → log the result.

---

## Creating an algo

1. Open **Algo Trading** from the command bar (`Cmd/Ctrl-K` → "Algo Trading").
2. Click **+ New**. The editor opens, pre-seeded from the widget's linked symbol.
3. Pick the **kind** (Single-symbol or Cross-sectional) and fill in the fields:

   **Single-symbol**
   - *Symbol / Asset* — what to trade (channel-linked, so it can follow other widgets).
   - *Strategy* — the template; its parameters (e.g. Fast/Slow SMA) appear below.
   - *Timeframe* — `1d` (daily, end-of-day) or intraday (`1h`…`1m`).
   - *Direction* — both / long only / short only.
   - *Position size (× equity)* — fraction of account equity for the position. `0.5` = a
     half-equity long when the signal is long; `1.0` = full.

   **Cross-sectional**
   - *Universe* — the stock pool.
   - *Factor* — the ranking signal.
   - *Mode* — long/short (dollar-neutral) or long-only.
   - *Top fraction* — how much of each tail to hold (e.g. `0.2` = top/bottom 20%).
   - *Gross (× equity)* — overall leverage of the book.

4. Set the **cadence** (see below) and optionally tighten **risk** (see below).
5. **Preview** computes the live signal and the orders it *would* submit — **without trading**.
   Use it to sanity-check before arming.
6. **Save**. The algo appears in the list, **disarmed**.

---

## Cadence — when it runs

- **Daily (after close)** — fires at most once per local trading day, and only **after** a
  configurable time. Default **16:10 America/New York**: daily bars aren't final until after the
  US close, so this keeps the signal off a half-formed same-day bar. Change the *After (local
  time)* field for other markets.
- **Interval** — fires every *N* seconds (min 10). Use for intraday/crypto algos. The runner's
  scheduling tick is ~30s, so very short intervals are effectively floored at one cycle per tick.

> A daily algo created at 2pm won't fire until after its close time today; an interval algo fires
> on the next tick.

---

## Arming, running, and the kill switch

- **Arm / Disarm** (per algo) — only **armed** algos run on their cadence. The green pulsing dot
  = armed.
- **Run** (per algo) — run one cycle immediately, regardless of cadence or arm state. Submits
  orders. Good for testing.
- **Pause all / Resume** (toolbar) — global kill switch. While **Paused**, no armed algo trades,
  but configs and arm states are preserved. The toolbar shows **Live** or **Paused**.

State is persisted: after a server restart, armed algos reload and resume automatically.

---

## Risk gates (always on)

Every cycle is gated before any order is placed:

- **Target-weight gate** — checks gross / net / per-name limits. If the target book breaches a
  limit the cycle is **rejected** (status shows the reason) and **nothing is submitted**.
- **Drawdown kill** — if the paper book's drawdown breaches `max_drawdown_kill` (default 20%),
  the cycle is **killed** and the algo is **auto-disarmed**.

**Defaults are kind-aware:** a single-symbol algo may hold one full-size name (per-name cap 1.0);
a cross-sectional book uses the diversified 0.20 per-name cap. Override either in the editor
(*Max position*, *Drawdown kill*) — leave blank to use the default.

---

## Reading the activity feed

Select an algo to see its **live signal** and **activity** (one card per cycle):

- **Status** — `ok` (orders submitted), `preview`, `rejected` (gate), `killed` (drawdown),
  `error` (no data / bad config), `no_data` / `no_weights`.
- **Signal chip** — LONG / SHORT / FLAT (single-symbol) or *N names* (cross-sectional).
- **Orders** — the submitted (or, for preview, intended) buys/sells; per-order errors show inline.
- **Equity** — account equity at that cycle.

The resulting positions, P&L, and order history live in the **Paper Trading** widget — open both
side by side. The runner trades the one shared paper book.

---

## Sim vs Alpaca — important

- **SIM**: fully local, frictionless by default (configure commission/slippage in Paper Trading).
  Reset anytime from the Paper Trading widget.
- **ALPACA**: an **armed** algo places **real Alpaca paper orders** on a schedule with no human in
  the loop. Use arm/disarm, Pause all, and the risk gates deliberately. Alpaca's own order
  history and realized P&L live on the Alpaca dashboard.

---

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Algo never fires | Not **armed**, runner **Paused**, or daily cadence's *after* time not reached yet. |
| Status `error: insufficient data` | Symbol/timeframe has too little history (needs ≥30 bars). |
| Status `rejected` | Target weights breach a risk limit — loosen *Max position* or reduce size. |
| Status `killed` + auto-disarmed | Drawdown breached `max_drawdown_kill`; review the book, then re-arm. |
| Cross-sectional cycle is slow | First run fetches the whole universe's history; subsequent runs are cached. |
| Drawdown kill never trips | Needs ≥2 equity snapshots; the runner records one per cycle, so it builds as the algo runs. |

---

## Power users — REST API

All under `/api/algo` (base `http://localhost:8050`):

| Method · Path | Purpose |
| --- | --- |
| `GET /strategies` | templates, factors, universes, broker |
| `GET /status` · `POST /pause` · `POST /resume` | runner state + global kill switch |
| `GET /algos` · `POST /algos` · `PUT /algos/{id}` · `DELETE /algos/{id}` | CRUD |
| `POST /algos/{id}/arm` · `/disarm` | per-algo enable/disable |
| `POST /algos/{id}/run` | run one cycle now (submits) |
| `POST /preview` | compute signal + intended orders (no trade) |
| `GET /algos/{id}/runs` | cycle log |

The cross-sectional engine and reconciliation reuse qhfi's backtest contracts
(`diff_to_orders`, `RiskGate`), so paper and backtest stay consistent. See
[ARCHITECTURE.md](../ARCHITECTURE.md) for the engine layering.
