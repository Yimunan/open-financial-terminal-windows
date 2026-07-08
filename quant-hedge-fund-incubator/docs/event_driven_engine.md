# Event-Driven Backtest Engine

**Status:** live · **Location:** `src/qhfi/backtest/eventdriven/` · **Date:** 2026-06-12

A handler/queue backtester that runs behind the same `Strategy` / `Scorecard` contracts as the
vectorized `BacktestEngine`. ARCHITECTURE.md flagged this as the one missing seam ("*an
event-driven engine would slot behind the same `Strategy`/`Scorecard` contracts*"); this document
describes what was built, how it maps to the existing engine, and the evidence that the two agree.

---

## 1. Why

The original `backtest/engine.py` is a **vectorized per-day loop**: iterate one shared daily index,
rebalance toward `weights.shift(signal_lag)`. It is fast and correct but has a fixed control flow —
there is no clean place to interleave discrete events (corporate actions, funding resets, intraday
bars) or instruments arriving on genuinely asynchronous clocks, and it is not the shape a live
trading loop takes.

The event-driven engine is the **textbook architecture**: a single time-ordered queue dispatching
`MarketEvent → SignalEvent → OrderEvent → FillEvent` to pluggable components. It is **additive** —
the vectorized engine and its tests are untouched — and produces the **same `BacktestResult`**, so
`Scorecard`, `walk_forward`, metrics, and tearsheets consume it with no change.

---

## 2. Architecture

```
                 ┌──────────────┐   MarketEvent    ┌──────────────┐
   prices panel ─►│ DataHandler  │─────────────────►│   EventLoop  │
                 └──────────────┘                   │  (heapq by   │
                                                    │ ts,priority) │
   ┌────────────────────────────────────────────────┴──────────────┐
   │  per timestamp t, in priority order:                            │
   │                                                                 │
   │  MarketEvent ─► Portfolio.on_market   (VM, financing,           │
   │                  │                     size the LAGGED target)  │
   │                  └─► OrderEvent ─► ExecutionHandler.execute      │
   │                                      └─► FillEvent ─► Portfolio  │
   │  MarketEvent ─► Strategy.on_market ─► SignalEvent ─► Portfolio   │
   │                                       (stored for t+lag)        │
   │  RecordEvent ─► Portfolio.record      (carry, re-mark, log row) │
   └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                           BacktestResult   (same type as BacktestEngine)
```

### Components (`src/qhfi/backtest/eventdriven/`)

| File | Component | Responsibility |
|---|---|---|
| `events.py` | `MarketEvent`, `SignalEvent`, `OrderEvent`, `FillEvent`, `RecordEvent` | Frozen dataclasses; class-level `PRIORITY` orders same-timestamp events |
| `data.py` | `DataHandler` protocol, `PanelDataHandler` | Streams bars in time order; only instruments that *printed* appear (async-safe) |
| `strategy.py` | `EventStrategy` (push ABC), `WeightStrategyAdapter` | New `on_market(event, book)` interface **+** adapter that replays any vectorized strategy's weights as signals |
| `portfolio.py` | `Portfolio` | The book + accounting brain — mirrors `BacktestEngine` step-for-step; buffers signals by heartbeat for the lag |
| `execution.py` | `ExecutionHandler` protocol, `SimulatedExecutionHandler` | Order → fill via slippage + commission (the main extension seam) |
| `engine.py` | `EventLoop`, `EventDrivenEngine` | Heapq dispatch + the facade with `run()` / `run_strategy()` |

### Reuse (no duplication of models)

The engine imports and reuses the existing `BacktestResult`, `ExecutionConfig`, `_round_to_lot`
(`backtest/engine.py`), `CompositeCostModel` (`backtest/costs.py`), `SlippageModel`/`FillTiming`
(`backtest/fills.py`), `FinancingModel` (`backtest/financing.py`), and `CompositeSizing`
(`portfolio/sizing.py`). Importing the shared `BacktestResult` is what guarantees evaluation-stack
compatibility *by construction*.

---

## 3. How it preserves the vectorized semantics

Two subtleties make the event engine reproduce the vectorized engine exactly:

1. **Heartbeat ordering.** Within a timestamp `t`, events drain in `PRIORITY` order:
   `MarketEvent(0) → OrderEvent(1) → FillEvent(2) → SignalEvent(4) → RecordEvent(5)`. So the bar's
   fills settle *before* equity is recorded, and the new signal is stored *before* the record seals
   the heartbeat. The next bar (`t+1`, a later timestamp) only pops after all of `t`'s derived
   events.
2. **Signal lag = one heartbeat.** The `Portfolio` buffers one signal per heartbeat; the target it
   executes on bar `t` is the signal received at `t − signal_lag`. This is the exact analogue of the
   vectorized engine's `weights.shift(signal_lag)` look-ahead guard (default `signal_lag = 1`: a
   signal from `t` fills at `t+1`'s close).

Per-bar accounting is identical: variation margin on margined positions → financing on the
pre-trade book → size the lagged target via `CompositeSizing` + no-trade band + lot rounding →
adverse-slippage fill + per-class commission → carry → re-mark to realized equity.

---

## 4. Equivalence — the correctness anchor

On dense daily data the event engine is **numerically identical** to `BacktestEngine` (same models,
same accounting order). Verified in `tests/test_eventdriven.py` and on real lake data:

| Case | Result |
|---|---|
| Single name, frictionless (`[100,110,121,133.1]`, full long) | equity & returns match to `1e-9` |
| 5 names, long/short, 10bps comm + 5bps slip + 50/100bps financing | equity/returns/turnover/commission/financing all `allclose` |
| NaN bar (per-instrument calendar gap) | position carried; equity matches |
| **Real data:** `ButterflyStrategy` MA/V/AXP, 4,581 days, $50M book | **identical** — Sharpe +0.266, final \$74,887,255, the same **11,379 trades** |

The vectorized engine's own pins (accounting identity `cash + pos·price == equity`, one-bar lag,
slippage/commission drag, short-borrow bleed, whole-lot rounding) are **re-run on the event engine**
and pass.

---

## 5. Performance

Pure-Python heap dispatch adds modest overhead — there is no algorithmic blow-up:

| Book | Vectorized | Event-driven | Ratio |
|---|---|---|---|
| 10 names × 1,000 days | 114 ms | 135 ms | 1.2× |
| 50 names × 2,000 days | 603 ms | 768 ms | 1.3× |
| 100 names × 4,000 days | 2.30 s | 2.81 s | 1.2× |

(all `match = True`). Use the **vectorized** engine for bulk research sweeps; reach for the
**event-driven** engine when you need its flexibility (below).

---

## 6. Usage

Drop-in (identical signature to `BacktestEngine.run`):

```python
from qhfi.backtest.eventdriven import EventDrivenEngine

result = EventDrivenEngine().run(weights, prices, universe)   # -> BacktestResult
Scorecard().grade(result)                                     # consumes it unchanged
walk_forward(strategy, prices, universe, EventDrivenEngine()) # drop-in here too
```

Native push strategy (reacts per bar):

```python
from qhfi.backtest.eventdriven import EventStrategy, SignalEvent, EventDrivenEngine

class EqualLong(EventStrategy):
    def on_market(self, event, book):                # book: read-only equity()/position()/last_price()
        ids = list(event.prices)
        return [SignalEvent(event.timestamp, {c: 1/len(ids) for c in ids})] if ids else []

EventDrivenEngine().run_strategy(EqualLong(), prices, universe)
```

Pluggable execution (the extension seam) — any object with `execute(order, instrument) -> FillEvent`:

```python
from qhfi.backtest.eventdriven import EventLoop, WeightStrategyAdapter
from qhfi.backtest.eventdriven.portfolio import Portfolio
from qhfi.backtest.eventdriven.data import PanelDataHandler

loop = EventLoop(PanelDataHandler(prices), WeightStrategyAdapter(weights),
                 Portfolio(instruments, ...), MyExecutionHandler(), instruments)
result = loop.run()
```

---

## 7. What it unlocks (and current limits)

**Unlocks** — the queue + handler seams make these incremental, not rewrites:
- Native discrete events (dividends, funding resets, earnings) as new `Event` subtypes on the heap.
- Asynchronous / mixed-frequency books (a bar only appears when an instrument prints — no dense
  union grid required).
- Custom execution models (latency, partial fills, market impact) via a new `ExecutionHandler`.
- The same shape a live paper/trading loop takes (`trading/loop.py`).

**Current limits** (deliberate, matching the vectorized engine):
- Order→fill resolves within a single heartbeat (daily close/next-open), not a true intraday path.
- `WeightStrategyAdapter` precomputes the full weights panel, so an adapted vectorized strategy is
  not *reactive* — only a native `EventStrategy` reacts to live book state mid-run.
- Single-process, in-memory; the `DataHandler` streams from a panel (no live feed adapter yet).

---

## 8. Tests

`tests/test_eventdriven.py` (9 tests): equivalence (single / multi-name+costs / NaN-carry),
accounting-identity + lag, short-borrow drag, lot rounding, a native push strategy, a custom
pluggable `ExecutionHandler`, and `Scorecard` + `walk_forward` compatibility. Full suite: **313
passed**, no regressions (the vectorized engine and its tests are untouched).
