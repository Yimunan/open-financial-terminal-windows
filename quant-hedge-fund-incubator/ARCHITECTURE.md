# Quant Hedge Fund Incubator — Architecture

A framework for **incubating** quantitative trading strategies: take an idea from
hypothesis → implementation → backtest → evaluation → paper trading, with LLM
research agents assisting at every step. Multi-asset (crypto / US equities / futures)
over one abstract core, daily/swing frequency, vectorized backtests. Research + paper
only — no live-money execution wiring (a gated live adapter is a deliberate future seam,
not built here).

## Design principles

1. **Asset-class agnostic core.** Everything above the data layer speaks in
   `Instrument` + normalized daily `Bars`. Crypto, equities, and futures differ only in
   *adapters* (data providers, cost models, calendars, brokers) — never in strategy or
   backtest code.
2. **Strategies are pure functions of data.** A strategy maps a price/feature panel to
   **target weights**. No I/O, no broker calls, no look-ahead. This makes them trivially
   backtestable, composable, and safe to generate with an LLM.
3. **One simulation path.** The same vectorized engine produces backtest, walk-forward,
   and paper-trade-shadow results. Paper trading is "run the engine for *today* and send
   the diff to a broker."
4. **Promotion is a gate, not a vibe.** A strategy advances through lifecycle states only
   by passing an explicit, recorded **scorecard**. Every experiment is persisted.
5. **LLM agents propose; the framework disposes.** Agents generate ideas and draft code,
   but generated code is sandboxed, only ever runs in backtest, and must clear the same
   scorecard as human work. No agent touches execution.

## Strategy lifecycle (the "incubator")

```
   IDEA ──► RESEARCH ──► IMPLEMENTED ──► BACKTESTED ──► VALIDATED ──► PAPER ──► (LIVE*)
    ▲          │             │               │              │           │
    └──────────┴─────────────┴───────── REJECTED / RETIRED ◄┴───────────┘
   (* live is a gated future state; not implemented in this scope)
```

Each transition is recorded in the **registry** with the artifact that justified it
(hypothesis text, code version, backtest id, scorecard). Promotion criteria are config,
not code.

## Layered architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CLI / Orchestration         qhfi backtest | research | paper | report      │
├──────────────────────────────────────────────────────────────────────────┤
│  Research agents (LLM)   ideation · codegen · critic   ─┐                    │
│    └─ httpx → vLLM proxy :8001 / LangGraph :8082 / crewAI :8083             │
├──────────────────────────────────────────────────────────────────────────┤
│  Incubator services                                                        │
│   ┌────────────┐  ┌──────────────┐  ┌───────────┐  ┌────────────────────┐ │
│   │  Strategy  │─►│   Backtest   │─►│ Evaluation│─►│  Registry          │ │
│   │  library   │  │   engine     │  │ scorecard │  │  (lifecycle + db)  │ │
│   └────────────┘  └──────────────┘  └───────────┘  └────────────────────┘ │
│   ┌────────────┐  ┌──────────────┐  ┌───────────┐                          │
│   │ Portfolio  │  │     Risk     │  │  Trading  │  (paper loop)             │
│   │ allocator  │  │     gates    │  │   loop    │                           │
│   └────────────┘  └──────────────┘  └───────────┘                          │
├──────────────────────────────────────────────────────────────────────────┤
│  Abstract core    Instrument · AssetClass · Calendar · Bars · TargetWeights │
├──────────────────────────────────────────────────────────────────────────┤
│  Data layer    DataProvider (protocol) + DataStore (parquet lake)           │
│    crypto:ccxt   equities:alpaca/yfinance   futures:ib_insync               │
│  Execution     Broker (protocol):  PaperBroker · AlpacaPaper · CcxtPaper     │
└──────────────────────────────────────────────────────────────────────────┘
```

## Modules (`src/qhfi/…`)

| Module | Responsibility | Key contract |
|---|---|---|
| `core` | Asset-agnostic domain types | `Instrument`, `AssetClass`, `Bars`, `TargetWeights` |
| `data` | Fetch + normalize + cache daily bars + PIT fundamentals | `DataProvider`, `DataStore`, `FundamentalsStore` |
| `factors` | Reusable signals + cross-sectional hygiene + factor diagnostics | `Factor.compute()`, `transforms.*`, `evaluation.information_coefficient()` |
| `strategy` | Strategy contract + library + registry | `Strategy.generate_weights()` |
| `backtest` | Vectorized portfolio simulation | `BacktestEngine.run()`, `CostModel` |
| `evaluation` | Metrics, tearsheets, promotion scorecard | `Scorecard.grade()` |
| `portfolio` | Combine strategies into a fund-level book + risk-based sizing | `Allocator.allocate()`, `CompositeSizing.target_units()` |
| `risk` | Pre-trade limits + kill switch | `RiskGate.check()` |
| `execution` | Broker abstraction (paper now) | `Broker` protocol |
| `trading` | Daily paper loop: data→signals→reconcile→orders | `PaperLoop.run_once()` |
| `research` | LLM agents + local-stack client | `ResearchAgent`, `LLMClient` |
| `registry` | Experiment + strategy lifecycle persistence | `StrategyRecord`, `LifecycleState` |
| `models` | Versioned trained-model artifact store + cards + stage lifecycle | `ModelRepository`, `ModelCard`, `ModelStage` |

## Factor research layer

Daily/cross-sectional work runs on **factors**: pure functions mapping a price/feature
panel to a raw score panel (dates × instrument). The flow is:

```
Factor.compute → winsorize → zscore/rank → neutralize(sector/beta) → combine → FactorStrategy → TargetWeights
                                                                  └─ evaluation: IC, quantile spread, decay, turnover ─┘
```

`factors.evaluation` grades a signal *before* it reaches a backtest — daily rank-IC and its
information ratio (`ic_summary`), monotonicity via `quantile_returns`/`spread`, horizon
`ic_decay` (half-life → viable holding period), and `autocorrelation` (turnover proxy). A
weak or fast-decaying factor is rejected here, cheaply, without a full simulation.
`FactorStrategy` is the bridge from this layer to the `Strategy` contract; most
cross-sectional strategies are an instance of it with different factor choices.

## Key data contracts

- **`Bars`** — per-instrument daily OHLCV `DataFrame`, UTC `DatetimeIndex`, adjusted for
  splits/dividends (equities) and rolled (futures) *inside the provider*. Strategies never
  see raw corporate actions.
- **Panel** — `dict[instrument_id, Bars]` or a wide `DataFrame` (dates × instruments) of a
  single field (e.g. close). The backtest engine works on wide frames.
- **`TargetWeights`** — wide `DataFrame` (dates × instruments), each row the desired
  fraction of book equity per instrument. Sign = direction, `abs().sum()` = gross. This is
  the *only* thing a strategy emits.

## Backtest execution model (granular, not a weight dot-product)

The engine runs a **per-day accounting loop in position-and-cash space** rather than the
naive `(weights × returns).sum()` shortcut, so the simulation reflects real-book mechanics
and stays consistent with what the paper loop can actually execute. Each day, in order:

1. **Mark** the book at the latest price (per-instrument calendar: an instrument with a NaN
   price that day is *carried* at its last mark, never traded — mixed 24/7-crypto + 5-day
   equity books behave correctly).
2. **Finance** the overnight book — short-borrow on short notional, leverage financing on
   borrowed cash (gross > equity), minus interest on idle cash (`financing.py`).
3. **Rebalance** toward the target row *that a signal dated `t − signal_lag` produced*
   (the structural look-ahead guard). For each instrument that trades today:
   target weight → target **units** via `lot_size` / `contract_multiplier`, rounded to whole
   lots unless `allow_fractional`; trade only the **delta** from the drifted position, and
   only if it exceeds the **no-trade band** (`rebalance_threshold`).
4. **Fill** at the bar close or the next bar's open (`fills.py`), at an *adverse*
   slippage-adjusted price — so slippage changes the new position's marked PnL, not just a
   cost line. Commission is charged per asset class (`costs.py`).
5. **Re-mark** at the close → realized equity and daily return (path-dependent, from actual
   equity — never a dot-product).

The result carries a full audit: equity, cash, gross/net exposure, separate
commission/slippage/financing series, per-instrument unit positions, and a trade log. Knobs
live under `backtest:`/`slippage:`/`financing:` in `config/settings.yaml`.

**Modeling choices made explicit** (the simplifications that remain): no intraday path
(close-to-close or open fills only); market impact is flat bps, not size/ADV-dependent;
borrow rates are uniform per book, not per-name; corporate actions and futures rolls are
resolved upstream in the data providers, not the engine.

## Asset-class taxonomy (multi-asset incl. FICC)

The instrument model uses **two orthogonal axes** so contract form and underlying are not
conflated (a Bund future and an S&P future share a form, not a class):

| `AssetClass` | `InstrumentForm` |
|---|---|
| equity · rates · credit · fx · commodity · crypto | cash · etf · future · perp · forward · swap |

From these the model **derives** the two things the engine branches on:

- **Funding** — `is_margined` (futures/perp/forward/swap) vs cash-funded (cash/etf/bonds).
  Margined instruments post no notional: the overnight move flows to cash as **variation
  margin**, and equity = cash + cash-funded holdings. Cash instruments debit full notional.
- **Risk basis** — `RiskBasis.DV01` for rates/credit, `NOTIONAL` otherwise. The
  `portfolio.sizing` layer routes a `TargetWeights` cell accordingly: a notional weight is a
  fraction of equity; a DV01 weight is a fraction of a DV01 risk budget
  (`dv01_budget_per_equity`), so $1m of 2Y and $1m of 30Y get risk-equalized, not
  notional-equalized.

**Carry as return** — the engine accepts a `carry` panel of daily rates (coupon accrual,
perp funding, FX swap points, commodity roll). Carry is income/cost on held notional, added
to PnL — essential because for FX and rates it often dominates price return.

**Calendars / costs** — each `AssetClass` maps to a trading calendar (crypto 24/7, FX 24/5,
equity/rates `XNYS`, commodity `CMES`); per-instrument NaN prices encode non-trading days
(positions carried, not traded). Commission dispatches per asset class with a bps fallback.
Position accounting respects `contract_multiplier`, `lot_size`, and `tick_size`.

**Equity classification & fundamentals** — equities carry an optional `EquityMeta` (GICS
sector/industry, country, market cap, ADV, index membership). `Universe.groups(level)` turns
it into the `{id: label}` map that `factors.transforms.neutralize()` consumes, so
sector-neutral signals need no external dict. Fundamental factors (`ValueFactor`,
`QualityFactor`) are `FundamentalFactor`s built from a **point-in-time** fundamentals panel
(`data.fundamentals.FundamentalsStore`) — values stamped at the report/knowable date, not
fiscal period-end, to avoid the classic equity look-ahead bug. *Open:* point-in-time index
membership (survivorship-free universe-as-of-date) and an `OPTION` form (non-linear, needs a
separate greeks path) are not built.

**FICC gaps still open** (honest): rates/credit need a real yield-curve + day-count layer
(DV01 here uses a supplied `modified_duration`, not a bootstrapped curve); multi-currency
accounting (FX conversion to a base currency) is modeled in the taxonomy via
`quote_currency`/`base_currency` but the engine still sums in one currency; per-name borrow
and live margin schedules are uniform/defaulted.

## LLM research integration (first-class)

The framework is a *client* of your existing local services (it does not embed LangGraph/
crewAI):

- **`LLMClient`** → OpenAI-compatible vLLM auto-swap proxy at `http://localhost:8001/v1`
  for direct completions (ideation, critique).
- **`LangGraphBridge`** → `POST http://localhost:8082/...` for multi-step research graphs.
- **`CrewAIBridge`** → `POST http://localhost:8083/...` for crew-style decomposition.

Agents:
- **`IdeationAgent`** — turns a theme/universe into testable hypotheses (structured output).
- **`CodegenAgent`** — drafts a `Strategy` subclass from a hypothesis; output is written to
  `strategy/library/` only after passing a **sandboxed** import + dry backtest.
- **`CriticAgent`** — reads a `BacktestResult` + scorecard and flags overfitting,
  look-ahead, regime dependence; gates promotion.

**Safety:** generated code is loaded in `research/sandbox.py` (restricted namespace, no
network/filesystem at import), runs only against the backtest engine, and is subject to the
identical scorecard. Agents are never wired to `execution`.

## Data taxonomy

Every *kind* of data the framework handles is classified along five orthogonal dimensions,
formalized in `data/taxonomy.py` (the `DATASETS` registry is the source of truth for what
exists, where it lives, and its status). This is distinct from `DataManager.catalog()`, which
inventories data *instances* (per-instrument parquet files).

- **Domain** — `market` · `reference` · `fundamental` · `carry` · `corporate_action` · `derived`
- **Frequency** — `static` · `quarterly` · `daily` · `intraday` · `tick`
- **PIT discipline** — `static` · `snapshot` (current-only, *not* backtest-safe) ·
  `point_in_time` (stamped at knowable date) · `revised`
- **Zone** — `raw` → `normalized` (canonical schema backtests consume) → `derived`
- **Asset class** — the `AssetClass` set the dataset applies to
- **Source** — a *structured, tiered* dimension (`Source` + `SourceTier`): `primary` (SEC
  EDGAR) · `exchange` (ccxt) · `broker` (Alpaca/IB) · `aggregator` (yfinance) · `reference`
  · `computed`. A dataset lists sources in preference order; `authoritative` is the
  system-of-record. Each source carries `pit_capable`. This is why `fundamentals` resolves
  to EDGAR (primary, PIT) over yfinance (aggregator, period-end) even though both supply it.

| dataset | domain | freq | PIT | status |
|---|---|---|---|---|
| `daily_bars` | market | daily | point-in-time¹ | live |
| `instrument_reference` | reference | static | snapshot² | live |
| `trading_calendar` | reference | static | static | stub |
| `fundamentals` | fundamental | quarterly | point-in-time | stub |
| `carry` | carry | daily | point-in-time | stub |
| `corporate_actions` | corporate_action | daily | point-in-time | live (folded into bars) |
| `factors` | derived | daily | point-in-time | live |

¹ split/div-adjusted, with adjustment factors applied retroactively (standard total-return
convention — a mild adjustment look-ahead). ² current values applied to history → the
survivorship + no-point-in-time-membership caveat. The PIT column is the contract that keeps
backtests honest: anything `snapshot` must not be treated as historical.

## Tech stack

- Python 3.11+, `pandas`/`numpy`/`polars`/`pyarrow`
- `pydantic` v2 + `pydantic-settings` + YAML for config
- `exchange-calendars` for trading calendars
- Data: `ccxt` (crypto), `alpaca-py` (equities + paper broker), `yfinance` (free EOD
  bootstrap), `ib_insync` (IBKR futures/equities, extra)
- `quantstats` tearsheets; `httpx` for the local LLM stack; `typer` + `rich` CLI
- Storage: parquet data lake (`data/`), SQLite registry (`registry`)
- Dev: `pytest`, `ruff`, `mypy`

## Out of scope (deliberate future seams)

- Live-money execution (interface exists via `Broker`; only paper impls provided).
- Intraday/HFT (engine is vectorized daily; an event-driven engine would slot behind the
  same `Strategy`/`Scorecard` contracts).
- Distributed/parallel backtests (single-process now; the registry already keys experiments
  for a future runner).
