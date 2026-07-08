"""Shipped example workflows, seeded into the saved-graph library on startup.

A fresh terminal has an empty `agent_graphs` table, so the Agent Workflow widget has nothing to
load. `seed_demo_workflows` saves these example specs into the library (idempotently) so they
appear under "Workflows ▾" and can be loaded + run with one click. The demo is the canonical
quant pipeline (data → strategy → portfolio → {backtest, execution}); it has no `llm` node so it
runs offline against local market data, and execution ships disarmed (arm="off" = dry-run only,
never places real orders).
"""

from __future__ import annotations

from typing import Any

# name → spec. Mirrors AgentGraphWidget.tsx `seedGraph()` so a new widget and the demo match.
DEMO_WORKFLOWS: dict[str, dict] = {
    "Demo · Momentum Pipeline": {
        "nodes": [
            {"id": "n1", "type": "data", "x": 30, "y": 150, "config": {"universe": "dow30"}},
            {"id": "n2", "type": "strategy", "x": 210, "y": 150,
             "config": {"factor": "momentum", "mode": "long_only", "top_pct": 0.2, "years": 3}},
            {"id": "n3", "type": "portfolio", "x": 400, "y": 150, "config": {"initial": 100000}},
            {"id": "n4", "type": "backtest", "x": 600, "y": 70, "config": {}},
            {"id": "n5", "type": "execution", "x": 600, "y": 240, "config": {"top_n": 5, "arm": "off"}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n4"},
            {"source": "n3", "target": "n5"},
        ],
    },
    # Autonomous research loop: the `research` node designs a factor experiment, backtests it,
    # grades it against the promotion scorecard, reflects, and redoes (≤5 iterations), then an
    # LLM node explains the winner. Demonstrates composing the self-directed loop with other nodes.
    "Demo · Research Loop": {
        "nodes": [
            {"id": "n1", "type": "research", "x": 60, "y": 150,
             "config": {"goal": "Find a robust long-short factor strategy that passes the promotion scorecard",
                        "max_iters": 3}},
            {"id": "n2", "type": "llm", "x": 320, "y": 150,
             "config": {"prompt": "The autonomous research loop produced this best result:\n\n{input}\n\n"
                                  "In 3-4 sentences, explain why it did or didn't pass the scorecard gate "
                                  "and suggest one concrete improvement to try next.",
                        "temperature": 0.4}},
            {"id": "n3", "type": "output", "x": 560, "y": 150, "config": {}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    },
    # Committee-guided refinement: the research loop finds a candidate, a strategy-approval
    # committee (CrewAI) reviews its robustness and names the most impactful change, then a
    # SECOND research pass — seeded with both the original best AND the committee critique
    # (fan-in into n3) — refines it. Output shows pass-1 vs the committee-improved pass-2.
    # (If the :8083 committee service is down, n2 degrades to "review unavailable" and the
    # refine pass still runs — the graph never aborts.)
    "Demo · Research + Committee Review": {
        "nodes": [
            {"id": "n1", "type": "research", "x": 40, "y": 90,
             "config": {"goal": "Find a robust long-short factor strategy that passes the promotion scorecard",
                        "max_iters": 2}},
            {"id": "n2", "type": "committee", "x": 300, "y": 90,
             "config": {"committee": "Investment Committee",
                        "prompt": "You are a quantitative strategy approval board reviewing a SYSTEMATIC "
                                  "cross-sectional factor strategy (not a single stock). Below is the best "
                                  "candidate from an automated research loop, with its backtest metrics and "
                                  "which promotion-scorecard checks it passed/failed:\n\n{input}\n\n"
                                  "Deliberate on whether it is robust enough to deploy. In your verdict set "
                                  "recommendation = deploy/refine/reject, and put the SINGLE most impactful "
                                  "improvement to raise its scorecard pass-rate (switch or blend a factor, "
                                  "lower turnover via a tighter quantile or longer rebalance, flip "
                                  "long-only/long-short, or reduce gross) in key_risks. Be specific and actionable."}},
            {"id": "n3", "type": "research", "x": 560, "y": 90,
             "config": {"goal": "Refine a cross-sectional factor strategy to address the review board's "
                                "concerns and pass more promotion-scorecard checks",
                        "max_iters": 2}},
            {"id": "n4", "type": "output", "x": 800, "y": 90, "config": {}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n1", "target": "n3"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n4"},
        ],
    },
    # Iterate-to-target portfolio builder: the research node runs up to 15 iterations, each one a
    # committee-reviewed redo, exploring factors + a market-timing overlay (none/trend/regime), and
    # stops as soon as it reaches Sharpe ≥ 2 — then saves the winner as a rebalancing portfolio. A
    # final committee node records an approval verdict on the book. (Both committee uses fall back to
    # the local LLM panel when the CrewAI :8083 service is down.) NOTE: Sharpe ≥ 2 is a high bar for
    # equity factors; if unreached the loop returns the best of 15 with target_met=false.
    "Demo · Committee Portfolio (timing → Sharpe≥2)": {
        "nodes": [
            {"id": "n1", "type": "research", "x": 40, "y": 90,
             "config": {"goal": "Build a rebalancing long-short portfolio with a market-timing overlay "
                                "that reaches Sharpe >= 2",
                        "max_iters": 15, "target_sharpe": 2, "committee": "on", "build_portfolio": "on"}},
            {"id": "n2", "type": "committee", "x": 320, "y": 90,
             "config": {"committee": "Investment Committee",
                        "prompt": "You are a quantitative strategy approval board. Below is the winning "
                                  "rebalancing portfolio (factors, timing overlay, metrics, scorecard) "
                                  "from an iterate-to-Sharpe-2 research loop:\n\n{input}\n\nIssue a final "
                                  "deploy/refine/reject verdict and put the single most important residual "
                                  "risk or improvement in key_risks."}},
            {"id": "n3", "type": "output", "x": 580, "y": 90, "config": {}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    },
}


def seed_demo_workflows(store: Any) -> None:
    """Save each demo workflow only if a graph of that name doesn't already exist.

    Idempotent and non-clobbering: won't duplicate on restart, and won't overwrite a workflow the
    user edited + saved under the same name.
    """
    for name, spec in DEMO_WORKFLOWS.items():
        if store.get_agent_graph(name) is None:
            store.save_agent_graph(name, spec)
    seed_demo_scenarios(store)
    seed_demo_algos(store)


# Example scenarios: a baseline preset and two market what-ifs over the same workflow.
DEMO_SCENARIOS: dict[str, dict] = {
    "Baseline · Dow momentum": {
        "description": "dow30 momentum long-only, 3y — no shock",
        "variables": {"universe": "dow30", "factor": "momentum", "mode": "long_only", "years": 3},
        "shocks": {"equity_pct": 0, "crypto_pct": 0, "vol_mult": 1},
    },
    "Stress · Equity -10%": {
        "description": "Same book, instantaneous -10% equity move",
        "variables": {"universe": "dow30", "factor": "momentum", "mode": "long_only", "years": 3},
        "shocks": {"equity_pct": -10, "crypto_pct": 0, "vol_mult": 1.5},
    },
    "Risk-off · Low-vol long-short": {
        "description": "Low-volatility factor, dollar-neutral on the S&P",
        "variables": {"universe": "sp500", "factor": "volatility", "mode": "long_short", "years": 5},
        "shocks": {"equity_pct": -5, "crypto_pct": 0, "vol_mult": 2},
    },
}


def seed_demo_scenarios(store: Any) -> None:
    """Seed example scenarios (idempotent — only if a scenario of that name is absent)."""
    existing = {s["name"] for s in store.list_scenarios()}
    for name, rec in DEMO_SCENARIOS.items():
        if name not in existing:
            store.save_scenario(name, {"name": name, **rec})


# Example algos so the Algo Trading widget isn't empty on a fresh terminal. Both ship DISARMED
# (`armed: False` — the runner only fires armed + due algos, so nothing trades on seed) and on the
# local `sim` sandbox book (account 1) — safe even when Alpaca paper keys are configured. Each
# mirrors the full `AlgoIn` field set (routers/algo.py), so a seeded record round-trips through the
# same create/edit/run path as a user-made one. `risk: {}` lets the runner apply its kind-aware
# default gates (a single full-size name for `template`, the diversified 0.20 cap for `xsection`).
DEMO_ALGOS: dict[str, dict] = {
    # template: a single-symbol StrategyLab signal (SMA crossover on AAPL daily bars). The runner
    # takes the template's live (last-bar) position as today's target weight for the one name.
    "demo-aapl-sma": {
        "name": "Demo · AAPL SMA cross",
        "kind": "template",
        "symbol": "AAPL",
        "asset": "equity",
        "timeframe": "1d",
        "strategy": "sma_cross",
        "params": {},  # runner fills the template's own defaults (fast/slow) from lab._template()
        "direction": "both",
        "universe": "dow30",
        "factor": "momentum",
        "mode": "long_short",
        "top_pct": 0.2,
        "size_pct": 1.0,
        "cadence": {"kind": "daily", "seconds": 300, "at": "16:10", "tz": "America/New_York"},
        "risk": {},
        "armed": False,
        "book": "sim",
    },
    # xsection: a cross-sectional factor book — long-only momentum on the Dow 30. The latest weight
    # row across the universe is today's target book (the same factor + weight builder the Backtest
    # widget uses).
    "demo-dow30-momentum": {
        "name": "Demo · Dow30 momentum (long-only)",
        "kind": "xsection",
        "symbol": "AAPL",
        "asset": "equity",
        "timeframe": "1d",
        "strategy": "sma_cross",
        "params": {},
        "direction": "both",
        "universe": "dow30",
        "factor": "momentum",
        "mode": "long_only",
        "top_pct": 0.2,
        "size_pct": 1.0,
        "cadence": {"kind": "daily", "seconds": 300, "at": "16:10", "tz": "America/New_York"},
        "risk": {},
        "armed": False,
        "book": "sim",
    },
}


def seed_demo_algos(store: Any) -> None:
    """Save each demo algo only if one with that id doesn't already exist.

    Idempotent and non-clobbering: won't duplicate on restart, and won't overwrite an algo the user
    edited under the same id (the demos use stable ids, unlike user-created algos' random uuids).
    """
    for algo_id, rec in DEMO_ALGOS.items():
        if store.get_algo(algo_id) is None:
            store.save_algo(algo_id, {**rec, "last_run": None})
