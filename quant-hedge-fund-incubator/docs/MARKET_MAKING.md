# Market Making — OBI + Avellaneda–Stoikov

A high-frequency, two-sided **quoting** market-maker for crypto, built as a parallel flavor of
the event-driven engine. It combines an **order-book-imbalance (OBI) / microprice** fair-value
signal with **Avellaneda–Stoikov (2008)** inventory management, and is backtested against a
limit-order **matching simulator** over recorded L2 depth.

This is a distinct family from the vectorized `Strategy.generate_weights` library: a maker posts
resting limit quotes and earns the spread, which the target-weight → market-order pipeline cannot
represent. It therefore has its own event types, strategy interface, engine, registry, and metrics.

## The model

Per book update, with inventory `q` (signed units, read live from the book):

```
microprice = (bid_px·ask_size + ask_px·bid_size) / (bid_size + ask_size)   # leans to the thin side
OBI        = (Σwᵢ·bidᵢ − Σwᵢ·askᵢ) / (Σwᵢ·bidᵢ + Σwᵢ·askᵢ)   ∈ [−1, 1]
s          = clamp( microprice + α·OBI·halfspread , best_bid, best_ask )    # fair value (center)
r          = s − q·γ·σ²·τ                                                    # reservation price (skew)
δ          = γ·σ²·τ + (2/γ)·ln(1 + γ/κ)                                      # optimal spread (width)
bid = r − δ/2 ,  ask = r + δ/2
```

- **OBI/microprice** sets the *center*; **AS** sets the *inventory skew* and *spread width*.
- **σ** = rolling realized vol of the mid (per-snapshot units); **κ** = order-arrival decay in
  `λ(δ)=A·e^{−κδ}`; **γ** = risk aversion; **τ** = constant horizon (infinite-horizon form — a
  continuously-running crypto maker has no terminal time).
- **Inventory limits** `±q_max` suppress the inventory-increasing side (one-sided quoting); with
  `inv_soften` that side's half-spread widens with `|q|` *before* suppression.

References: Avellaneda & Stoikov (2008) *High-frequency trading in a limit order book*; Stoikov
(2018) *The micro-price*; Cartea, Jaimungal & Penalva (2015) *Algorithmic and High-Frequency
Trading*; Gould et al. (2013) *Limit order books*.

## The strategies (all `QuotingStrategy`, registered in `mm_registry`)

| Strategy | Idea | Params |
| --- | --- | --- |
| **`SymmetricMM`** | The naive baseline: a constant half-spread (bps) around the mid, no skew/signal, hard inventory limit only. The control a smarter quoter must beat. | `half_spread_bps`, `q_max` |
| **`LinearInventoryMM`** | Practical, **scale-free** (everything in bps×mid): base spread + linear **inventory skew** `(q/q_max)·skew_bps` (shift the center to flatten toward zero) + **OBI tilt** + optional vol-widening. Works out of the box on any asset. | `half_spread_bps`, `skew_bps`, `obi_alpha`, `vol_gain`, `q_max` |
| **`AvellanedaStoikovMM`** | The textbook optimal quoter (reservation price + closed-form spread). | `gamma`, `kappa`, `obi_alpha`, `q_max` |
| **`AlphaQuoterMM`** | LinearInventory + a **calibrated predictive OBI overlay**: shift fair value by `mid·αbps·OBI` (from `forward_return_on_obi`) to quote *ahead* of the imbalance move. Passive (default) it *dodges* adverse selection; with **taker mode** (`take_threshold_bps>0`) it *crosses the spread to capture* the move when the predicted edge beats the crossing cost. `alpha_bps=0` → reduces to LinearInventoryMM. | `…, alpha_bps`, `alpha_gain`, `take_threshold_bps`, `taker_fee_bps` |

> **Parameter-scaling caveat:** `AvellanedaStoikovMM`'s spread/skew are in **absolute price units**,
> so `γ`/`κ` must be tuned to the asset's price + vol (use `calibrate_as_params.py`; e.g. a $100
> asset with cents spreads needs `κ` in the hundreds). `SymmetricMM`/`LinearInventoryMM` are
> bps-parametrized and need no per-asset calibration — prefer them as the practical default.

## Code map

| Concern | Location |
| --- | --- |
| Signal math (OBI, microprice, σ/κ, `forward_return_on_obi`) | `qhfi/data/microstructure.py` |
| Live / paper quoting loop (`PaperQuotingLoop`, risk gates, kill-switch) | `qhfi/trading/quoting_loop.py` |
| Events (`BookEvent`, `QuoteEvent`, `TradeEvent`) | `qhfi/backtest/eventdriven/events.py` |
| Strategy interface (`QuotingStrategy.on_book`) | `qhfi/backtest/eventdriven/strategy.py` |
| Matching / fills (`LimitOrderMatchingHandler`) | `qhfi/backtest/eventdriven/matching.py` |
| Book replay (`BookReplayDataHandler`) | `qhfi/backtest/eventdriven/data_book.py` |
| Engine (`MarketMakingEngine`, `QuotingEventLoop`) | `qhfi/backtest/eventdriven/engine.py` |
| Strategies (`AvellanedaStoikovMM`, `LinearInventoryMM`, `SymmetricMM`) | `qhfi/strategy/library/mm/` |
| Registry | `qhfi/strategy/mm_registry.py` |
| Metrics + scorecard | `qhfi/evaluation/mm_metrics.py`, `MarketMakingScorecard` |
| Data stores (`OrderBookStore`, `TradeStore`) | `qhfi/data/highfreq.py` |
| Recorders / calibration | `scripts/pull_orderbook_stream.py`, `scripts/pull_trades_stream.py`, `scripts/calibrate_as_params.py` |

## Fill model

`LimitOrderMatchingHandler` holds resting quotes between heartbeats. A passive fill executes
*at the quote price* (no slippage), paying only the maker commission; adverse selection emerges
naturally and is measured by markout. Two modes:

- **`cross`** (default, deterministic): a resting bid fills when a later best-ask crosses it (a
  sweep) or a trade prints at/through it. Supports partial fills and an optional queue model
  (fills gated until volume ahead at our price is depleted).
- **`intensity`** (seeded): per-book fill hazard `1 − exp(−A·e^{−κδ}·Δt)` — a sensitivity tool
  when no trade tape exists.

**Marketable (taker) orders.** A posted quote priced *through* the opposing touch
(`bid_px ≥ best_ask` / `ask_px ≤ best_bid`) is a taker order: `post()` fills it immediately at that
touch, charging the `taker_cost_model` fee and booking the touch-vs-mid distance as slippage (the
spread crossed). This lets a strategy *capture* a strong signal instead of only quoting around it —
the lever behind `AlphaQuoterMM`'s taker mode.

## Live / paper trading

`PaperQuotingLoop` ([trading/quoting_loop.py](../src/qhfi/trading/quoting_loop.py)) is the paper
stage: it drives a `QuotingStrategy` over a stream of `BookEvent`s (a live ccxt-pro
`watch_order_book` feed, or a recorded/synthetic replay), fills resting quotes against each update
with the **same matcher the backtest uses**, and tracks inventory/cash/PnL through the shared
`Portfolio` accounting — so paper and backtest can't drift. Fills are *simulated against the real
book* (no orders leave the machine). On top of the engine it adds the two things a live loop needs:

- **Risk gates** — a hard `max_inventory` cap that overrides the strategy, and a `max_drawdown_kill`
  **kill-switch** that cancels all quotes and stops once equity falls below a fraction of its peak.
- **Incremental `on_book(event)` + `state()`** — a per-heartbeat snapshot (inventory, equity, PnL,
  fills, halted) for live monitoring; `run(events)` drives a finite replay.

```python
loop = PaperQuotingLoop(strategy, instrument, initial_equity=1e6,
                        risk=QuotingRiskLimits(max_inventory=30, max_drawdown_kill=0.05))
for ev in book_event_stream:        # live ccxt-pro feed or BookReplayDataHandler(...).stream()
    state = loop.on_book(ev)        # settle fills → re-mark → gate risk → re-quote
```

## Workflow

```powershell
# 0. Offline demo — compare all three quoters on synthetic books + an inventory-skew sweep
#    (no recorded data needed; the fastest way to see the engine + metrics end-to-end).
.venv\Scripts\python.exe scripts\build_market_maker.py
```

```powershell
# 1. Record real depth + the trade tape (run durably; Scheduled Task / NSSM). Data accrues forward.
.venv\Scripts\python.exe scripts\pull_orderbook_stream.py --interval 1.0 --levels 20
.venv\Scripts\python.exe scripts\pull_trades_stream.py   --interval 1.0

# 2. Calibrate σ, κ, A once enough has accrued.
.venv\Scripts\python.exe scripts\calibrate_as_params.py --symbol BTC/USDT
# or:  qhfi mm calibrate --symbol BTC/USDT

# 3. Backtest the quoting strategy and print the market-making panel.
qhfi mm backtest --symbol BTC/USDT --strategy AvellanedaStoikovMM --gamma 0.1 --kappa 1.5 --q-max 50
```

The panel reports: P&L Sharpe, **spread captured (bps)**, **fill ratio**, **inventory** mean/std/
max + **AR(1) half-life**, the **markout curve** (`markout_h_bps`, the adverse-selection
diagnostic), fees, and **net edge (bps)**.

## Risk caveats (read before trusting a number)

1. **No historical depth is replayable retroactively** — the recorders accumulate it going
   forward. Until enough has accrued, validate against the synthetic-book tests, which pin
   *correctness*, not realism.
2. **Without the trade tape, fills are inferred from coarse book-snapshot transitions** and will
   mis-state fill rate and adverse selection. Record `pull_trades_stream.py` early.
3. **κ/σ estimated from snapshots are biased** (snapshot transitions conflate cancels, fills,
   replenishment). Report results across a **κ range**, not a point estimate.
4. **Queue-position modeling without per-order data is the least trustworthy component** — keep
   `queue_model` a deliberate toggle and validate only against the synthetic oracle.
