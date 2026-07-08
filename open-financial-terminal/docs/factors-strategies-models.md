# Factors, Strategies & Models

The terminal ships three dockable **registry** modules — **Factor Library**, **Strategy
Library**, and **Model Repository** — that let you browse what the engine offers, author your
own, and bundle a research setup for reuse. They are "developed separately" from the live
engine: built-ins come straight from **qhfi**, while custom records live in the terminal's
own SQLite store and (for strategies) are registered back into the engine so they actually
run.

Each module also has an **✦ Summarize** action that asks the local LLM to read your current
registry and write a short natural-language overview — what's present, the themes, and the
gaps. See [Summarize](#summarize) below.

Backend: [`services/registry.py`](../backend/app/services/registry.py) +
[`routers/registry.py`](../backend/app/routers/registry.py) (`/api/registry/*`).
Frontend: `widgets/{FactorLibrary,StrategyLibrary,ModelRepository}.tsx`.

---

## Factors

A **factor** scores and ranks instruments *cross-sectionally* — every name in a universe gets
a number, the engine sorts by it, and a strategy goes long the top and (optionally) short the
bottom. A factor's **direction** says which end is bullish: `high=long` (e.g. momentum) or
`low=long` (e.g. cheapness). Factors are the raw signal; turning a ranking into positions is
the strategy's job.

The Factor Library shows three groups:

| Group | Source | Editable | Use |
|-------|--------|----------|-----|
| **Built-in** | `services/factors.CATALOG` — the curated catalog (price momentum, value/quality, 6 Alpha101) | read-only | Backtest agent / Screener |
| **qhfi engine** | live `qhfi.factors` registry (built-ins + anything a linked dir `@register`ed) | read-only | Backtest widget / agent |
| **Custom** | your records in the store (`custom_factors` table) | author in the Sandbox | Backtest / Lab |

- **Built-in** factors are the catalog the Screener and Backtest agent understand by name
  ("momentum long-short on the dow").
- **qhfi engine** factors are whatever is registered in qhfi's own factor registry at runtime,
  including any `@register`-decorated factor you drop into the **linked factors directory**
  (set under Settings → Linked qhfi directories, then *Rescan*). `source: linked` marks
  yours; `source: builtin` is qhfi's.
- **Custom** factors are authored in the **Sandbox** as a small piece of sandboxed Python
  (validated by the `agent_code` allowlist — no imports, no dunder access). Click **＋ New** to
  open the Sandbox, or select a saved custom factor and **Open in Sandbox** to run/edit it.

> Custom factors are currently definition + sandbox-run; wiring an arbitrary custom factor
> into the cross-sectional engine (the qhfi factor protocol) is a follow-up. Custom
> **strategies**, by contrast, are fully runnable (below).

## Strategies

A **strategy** turns signals into trades. The terminal has two distinct flavours, reflecting
the two backtest engines:

1. **Single-symbol lab strategies** — event-driven rules on one instrument's bars (SMA/EMA
   cross, RSI reversion, MACD, Bollinger), with stop-loss / take-profit, commission, sizing,
   and leverage. These run in the **Strategy Lab** (`services/strategy_lab.py`).
2. **qhfi-engine portfolio strategies** — cross-sectional strategies that take a *factor* +
   *universe* and run through qhfi's accounting `BacktestEngine` (long-short or long-only, with
   real commission/slippage/financing). These run in the **Backtest** widget.

The Strategy Library groups them the same way as factors:

| Group | Source | Editable | Runs in |
|-------|--------|----------|---------|
| **Built-in (single-symbol lab)** | `strategy_lab` templates | read-only | Lab — *Test on {symbol}* |
| **qhfi engine — portfolio** | `engine_strategy` (built-ins + linked dir) | read-only | Backtest — *Run on {universe}* |
| **Custom** | store (`custom_strategies` table) | author in the Sandbox | Lab |

- **Custom strategies are runnable.** A custom strategy is sandboxed Python signal code that
  sets `result` to a per-bar `{-1, 0, +1}` array from `close`/`high`/`low` and its params.
  On save it's validated and **registered into the lab** (`strategy_lab.register_custom`), so
  *Test on {symbol}* works immediately. Persisted custom strategies are re-registered on
  backend startup, so they survive restarts.
- **qhfi-engine strategies** come from qhfi's strategy registry; drop a `@register`ed strategy
  into the **linked strategies directory** to add your own. Select one to pick a universe /
  mode / years and **Run** it through the engine.

## Models

A **model** is a saved *research bundle* — a named pointer to a **factor + strategy + universe
+ mode + params**, plus free-form tags and notes. It is not a trained artifact; it's a
reusable recipe so you don't have to re-pick the same combination every time. The Model
Repository has two tabs:

### Bundles

Your own bundles (`models` table), fully searchable across name, factor, strategy, universe,
tags, and notes. **＋ New model** opens an editor where you choose a factor and strategy (from
the live registries), a universe and mode, and add tags/notes. Use bundles to capture "the
setup that worked" and hand it to the Backtest agent or a teammate.

### Trained models

A read-only view of a linked **qhfi `ModelRepository`** — actual trained-artifact versions with
framework / domain / asset-class / metrics and a **stage** (`dev` → `staging` → `production` →
`archived`). You can **promote** a version between stages here. Point the models directory at a
qhfi ModelRepository root under Settings → Linked qhfi directories; if it's empty or unset the
tab says so.

---

## Linked directories

All three modules can read artifacts you develop **separately in the qhfi repo**. The defaults
point at the matching qhfi source packages (factors, strategy library) and the qhfi
ModelRepository artifact root; override any of them under **Settings → Linked qhfi
directories**. Drop-in `*.py` files are AST-parsed for listing (never executed) and imported
on demand so their `@register` decorators take effect — the same trust model as qhfi's
pickle-backed model store. Files inside the installed qhfi package are skipped (already
imported at startup).

## Authoring (the Sandbox)

Custom factors and strategies are authored in the **Sandbox** widget, not inline in the
libraries — the libraries are for browsing and launching. The Sandbox runs your Python under
the `agent_code` allowlist (no imports, no dunder/attribute escapes, a curated namespace with
`pd`/`np`/`math` and read-only `bars()`/`quote()` helpers). Assign `result` (and an optional
`summary`); the value is JSON-encoded for the engine.

## Summarize

Each module's **✦ Summarize** button calls `POST /api/registry/summarize` with the module
`kind` (`factors` | `strategies` | `models`). The backend builds a compact inventory of your
current registry — counts and a per-item line (name, kind/direction/label, source, custom
descriptions, model factor/strategy/universe/tags) — and asks the local LLM
(`registry.summarize_registry` → `LLMClient.complete`) for a tight overview: what you have,
the dominant themes/categories, and notable gaps. It's a read-only convenience for orienting
yourself in a large registry; nothing is persisted and it is not investment advice.
