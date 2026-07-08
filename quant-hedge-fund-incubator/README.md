# Quant Hedge Fund Incubator (`qhfi`)

A framework for incubating quantitative trading strategies end-to-end:

> **idea → research → backtest → evaluation → paper trading**

with LLM research agents assisting at every stage. Multi-asset (crypto / US equities /
futures) over one abstract core, daily/swing frequency, vectorized backtests,
**research + paper only** (no live-money execution).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Quickstart

```bash
# 1. install (editable, with dev tools)
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -e ".[dev]"

# 2. configure
copy .env.example .env                            # set API keys / LLM endpoints

# 3. pull data and run the example strategy
qhfi data pull --universe config/instruments/crypto_majors.yaml
qhfi backtest run --strategy momentum --universe crypto_majors

# 4. grade it against the promotion scorecard
qhfi report scorecard --backtest <id>

# 5. ask an LLM agent for fresh hypotheses (uses your local vLLM proxy)
qhfi research ideate --theme "cross-sectional momentum in liquid crypto"

# 6. paper-trade a validated strategy (one daily cycle)
qhfi paper run-once --strategy momentum --universe crypto_majors --broker paper
```

## Strategy lifecycle

`IDEA → RESEARCH → IMPLEMENTED → BACKTESTED → VALIDATED → PAPER → (LIVE*)`

Advancement is gated by a recorded **scorecard**; every experiment is persisted to the
registry. `(*)` live is a future, gated state — not built in this scope.

## Layout

```
src/qhfi/
  core/        domain types (Instrument, Bars, TargetWeights)
  data/        providers (ccxt / alpaca / yfinance / ib) + parquet store
  strategy/    Strategy contract + library + registry
  backtest/    vectorized engine + cost models + walk-forward
  evaluation/  metrics + tearsheets + promotion scorecard
  portfolio/   capital allocation across strategies
  risk/        pre-trade gates + kill switch
  execution/   Broker protocol + paper brokers
  trading/     daily paper loop
  research/    LLM agents (ideation/codegen/critic) + local-stack client
  registry/    experiment + lifecycle persistence
```

## Status

Scaffold / framework skeleton. Module **contracts** (protocols, ABCs, dataclasses) are
defined; concrete provider/broker/engine bodies are stubbed with `NotImplementedError`
and TODOs. Build order suggestion: `core → data → backtest → evaluation → strategy
library → registry → research → trading`.
