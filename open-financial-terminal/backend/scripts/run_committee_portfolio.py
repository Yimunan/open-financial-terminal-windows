"""Durable driver for the committee-guided, timing-aware, Sharpe>=2 portfolio loop.

Runs the SAME phase functions as research_loop.run_loop_sync (no logic divergence) but writes a
JSONL checkpoint after every iteration, so a long run's progress is observable/durable and the
best portfolio is built+saved at the end. Invoked by the /loop task; not part of the app.

Usage: python -m scripts.run_committee_portfolio
"""

from __future__ import annotations

import json
from pathlib import Path

import app.services.research_loop as rl
from app.deps import (
    get_data_manager, get_fundamentals_provider, get_fundamentals_store,
    get_llm_client, get_llm_model, get_store,
)

GOAL = ("Build a rebalancing long-short portfolio with a market-timing overlay that reaches "
        "Sharpe >= 2")
TARGET_SHARPE = 2.0
MAX_ITERS = 15

_OUT = Path(__file__).resolve().parent / "_committee_portfolio_progress.jsonl"


def log(obj: dict) -> None:
    with _OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def main() -> None:
    _OUT.write_text("", encoding="utf-8")  # fresh run
    deps = rl.RLDeps(
        dm=get_data_manager(), fstore=get_fundamentals_store(), fprov=get_fundamentals_provider(),
        llm=get_llm_client(), model=get_llm_model(), store=get_store(),
    )
    memory = rl.LoopMemory(goal=GOAL)
    inventory = rl._analyze(deps)
    log({"event": "start", "goal": GOAL, "target_sharpe": TARGET_SHARPE, "max_iters": MAX_ITERS,
         "universes": inventory["universes"], "factors": inventory["factor_keys"]})

    target_met = False
    for i in range(MAX_ITERS):
        exp = rl._design(deps, memory, inventory)
        try:
            payload, result, oos = rl._generate(deps, exp)
        except Exception as e:  # noqa: BLE001 - one bad iteration never aborts the run
            memory.history.append(rl.IterationRecord(i, exp, None, float("-inf"), None,
                                                     error=f"{type(e).__name__}: {e}"))
            log({"i": i, "error": f"{type(e).__name__}: {e}",
                 "experiment": {"factors": exp.factors, "mode": exp.mode, "universe": exp.universe,
                                "timing": exp.timing}})
            continue
        scored = rl._evaluate(result, oos, rl.get_universe(exp.universe))
        rec = rl.IterationRecord(i, exp, scored, rl._objective(scored), payload)
        memory.history.append(rec)
        if memory.best is None or rec.objective > memory.best.objective:
            memory.best = rec
        log({"i": i, "label": rl._compact(rec)["label"], "timing": exp.timing,
             "sharpe": scored.metrics.get("sharpe"), "calmar": scored.metrics.get("calmar"),
             "max_drawdown": scored.metrics.get("max_drawdown"),
             "ann_turnover": scored.metrics.get("ann_turnover"),
             "oos_ratio": scored.oos_sharpe_ratio, "n_checks": scored.n_checks_passed,
             "passed": scored.passed, "best_sharpe": rl._best_sharpe(memory)})
        if rl._best_sharpe(memory) >= TARGET_SHARPE:
            target_met = True
            log({"event": "target_met", "i": i, "best_sharpe": rl._best_sharpe(memory)})
            break
        if i < MAX_ITERS - 1:
            memory.guidance = rl._committee_guidance(deps, memory) or memory.guidance
            log({"i": i, "event": "committee_guidance", "guidance": memory.guidance})
            memory.last_reflection = rl._reflect(deps, memory)

    portfolio_name = None
    if memory.best and memory.best.result_payload:
        try:
            port = rl._build_portfolio(deps, memory.best)
            portfolio_name = port.get("name")
            log({"event": "portfolio_built", "name": portfolio_name, "mode": port.get("mode"),
                 "rebalance": port.get("rebalance"), "timing": port.get("timing"),
                 "exposures": port.get("exposures"),
                 "priced": (port.get("valuation") or {}).get("priced"),
                 "allocations": port.get("allocations")})
        except Exception as e:  # noqa: BLE001
            log({"event": "portfolio_error", "error": f"{type(e).__name__}: {e}"})

    best = rl._compact(memory.best) if memory.best else None
    log({"event": "done", "target_met": target_met, "n_iterations": len(memory.history),
         "best": best, "best_sharpe": rl._best_sharpe(memory) if memory.best else None,
         "portfolio_name": portfolio_name})


if __name__ == "__main__":
    main()
