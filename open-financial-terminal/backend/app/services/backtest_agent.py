"""Autonomous backtest agent: drives factor / strategy-lab backtests from a chat goal.

A bounded ReAct loop (same shape as `agent_coder`): each turn the local LLM emits ONE JSON
action — `run` a backtest with a set of params, or `done` naming the best run. We validate/clamp
the params to the live enums + ranges (like `assistant.nl_to_screen`), execute via the existing
`backtest.run_backtest` / `strategy_lab.run_lab`, and stream the run's headline metrics + full
result to the UI. For a single explicit config the agent runs once; for "find the best / compare"
it tries several and picks a winner. Bounded by `_MAX_RUNS` / `_MAX_STEPS`.
"""

from __future__ import annotations

import asyncio
import functools
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.services import backtest as bt
from app.services import engine_strategy as eng
from app.services import factors as fac
from app.services import portfolio_book as pb
from app.services import registry as reg
from app.services import strategy_lab as lab
from app.services.agent_assistant import _strip_to_json
from app.services.universe import get_universe, list_universes

# One backtest per prompt: resolve the request into a single config, run it once, then done.
# (Hard cap — even if the model tries to sweep, only the first successful run is kept.)
_MAX_RUNS = 1
_MAX_STEPS = 6

_MODES = ("long_only", "long_short")
_DIRECTIONS = ("long_only", "short_only", "both")


@dataclass
class BTDeps:
    dm: Any
    fstore: Any
    fprov: Any
    llm: Any
    model: str
    store: Any = None  # TerminalStore — needed by the portfolio engine (models + saved books)


_SOURCES = ("factor", "strategy", "model")


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def _pct(v: Any) -> float:
    """A stop/target as a fraction in [0,1]. Accepts "2" (meaning 2%) → 0.02."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f > 1:
        f = f / 100.0
    return min(1.0, max(0.0, f))


# ── factor engine ────────────────────────────────────────────────────────────────
def _factor_run(args: dict, deps: BTDeps) -> tuple[str, dict, dict, dict]:
    universes = list_universes() or ["dow30"]
    factor = args.get("factor", "momentum")
    if factor not in fac.CATALOG:
        factor = "momentum"
    universe = args.get("universe", "dow30")
    if universe not in universes:
        universe = "dow30" if "dow30" in universes else universes[0]
    mode = args.get("mode", "long_short")
    if mode not in _MODES:
        mode = "long_short"
    top_pct = _clamp(args.get("top_pct", 0.2), 0.05, 0.5, 0.2)
    years = int(_clamp(args.get("years", 3), 1, 10, 3))
    start = args.get("start") or None
    end = args.get("end") or None
    rebalance = str(args.get("rebalance", "monthly")).lower()
    if rebalance not in ("monthly", "quarterly", "annual"):
        rebalance = "monthly"
    timing = _parse_timing(args.get("timing"))

    out = bt.run_backtest(
        deps.dm, deps.fstore, deps.fprov, universe, factor, mode,
        top_pct, years, 100_000.0, True, start=start, end=end, rebalance=rebalance, timing=timing,
    )
    if "error" in out:
        raise ValueError(out["error"])
    m = out.get("metrics", {})
    rob = out.get("robustness", {})
    params = {"universe": universe, "factor": factor, "mode": mode, "top_pct": top_pct, "years": years}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    if rebalance != "monthly":
        params["rebalance"] = rebalance
    applied = out.get("timing")  # present only if the overlay actually applied (benchmark resolved)
    if applied:
        params["timing"] = applied.get("kind")
    metrics = {
        "sharpe": m.get("sharpe"), "cagr": m.get("cagr"), "max_drawdown": m.get("max_drawdown"),
        "psr": rob.get("psr"), "dsr": rob.get("dsr"),
    }
    label = f"{factor} · {mode} · {universe} · {years}y"
    if rebalance != "monthly":
        label += f" · {rebalance}"
    if applied:
        label += f" · {applied.get('kind')} timing"
    return label, params, metrics, out


def _parse_timing(t: Any) -> dict | None:
    """Normalize a timing overlay spec: a string ("trend"/"regime"/"none") or a dict. The service
    re-clamps all numeric params, so this just routes the kind and passes optional knobs through."""
    if isinstance(t, str):
        t = {"kind": t}
    if not isinstance(t, dict):
        return None
    kind = str(t.get("kind", "")).lower()
    if kind == "trend":
        spec: dict = {"kind": "trend"}
        if t.get("ma") is not None:
            spec["ma"] = t["ma"]
        if t.get("floor") is not None:
            spec["floor"] = t["floor"]
        return spec
    if kind == "regime":
        return {"kind": "regime"}
    return None


def _factor_system() -> str:
    factors = ", ".join(f"{k} ({v['label']})" for k, v in fac.CATALOG.items())
    universes = ", ".join(list_universes() or ["dow30"])
    return (
        "You are a quantitative backtesting agent for cross-sectional FACTOR strategies. Each turn "
        "emit ONE JSON object:\n"
        '{"thought":"<short>","tool":"run","args":{"universe":<name>,"factor":<key>,'
        '"mode":"long_only"|"long_short","top_pct":0.05-0.5,"years":1-10,'
        '"rebalance":"monthly"|"quarterly"|"annual"(optional, default monthly),'
        '"timing":{"kind":"trend","ma":200,"floor":0}|{"kind":"regime"}(optional),'
        '"start":"YYYY-MM-DD"(optional),"end":"YYYY-MM-DD"(optional)}}\n'
        'or finish: {"thought":"...","done":true,"message":"<summary>","best_id":"<run id like r2>"}\n\n'
        f"Factors: {factors}.\nUniverses: {universes}.\n\n"
        "Add `rebalance` only when the user asks for a non-monthly cadence (quarterly/annual). Add "
        "`timing` only when the user asks for market timing / a trend filter / regime de-risking: "
        "`trend` = scale to cash when the benchmark is below a moving average (ma, e.g. 200-day; "
        "floor = exposure when below, default 0); `regime` = a volatility-regime overlay that cuts "
        "exposure in turbulent regimes. Omit `timing` for a plain always-invested backtest.\n\n"
        "Policy: resolve the user's request into EXACTLY ONE configuration, run it once, then finish "
        "with done (best_id = that run). Do NOT sweep or try multiple configurations — even if the "
        "request says 'find the best' or 'compare', pick the single most appropriate config and run "
        "only that one. After the run you get an OBSERVATION with the run id + metrics. Reply with "
        "ONLY the JSON action."
    )


# ── strategy-lab engine ──────────────────────────────────────────────────────────
def _lab_run(args: dict, deps: BTDeps, context: dict) -> tuple[str, dict, dict, dict]:
    strat = args.get("strategy", "sma_cross")
    if strat not in lab.TEMPLATES:
        strat = "sma_cross"
    tmpl = lab.TEMPLATES[strat]
    raw_params = args.get("params") or {}
    params: dict = {}
    for p in tmpl.params:
        params[p.key] = _clamp(raw_params.get(p.key, p.default), p.min, p.max, p.default)

    symbol = (args.get("symbol") or context.get("symbol") or "AAPL").upper()
    asset = context.get("asset", "equity")
    timeframe = args.get("timeframe") or context.get("timeframe") or "1d"
    direction = args.get("direction", "long_only")
    if direction not in _DIRECTIONS:
        direction = "long_only"
    sl = _pct(args.get("sl_pct", 0))
    tp = _pct(args.get("tp_pct", 0))
    lev = _clamp(args.get("leverage", 1), 1.0, 5.0, 1.0)
    size = _clamp(args.get("size_pct", 1), 0.1, 1.0, 1.0)
    comm = _clamp(args.get("commission_bps", 5), 0.0, 50.0, 5.0)
    years = int(_clamp(args.get("years", 3), 1, 10, 3))

    out = lab.run_lab(
        deps.dm, symbol=symbol, asset=asset, timeframe=timeframe, strategy=strat, params=params,
        direction=direction, sl_pct=sl, tp_pct=tp, initial=100_000.0, commission_bps=comm,
        size_pct=size, leverage=lev, years=years,
    )
    s = out.get("stats", {})
    pretty = {
        "symbol": symbol, "strategy": strat, **params, "direction": direction,
        "timeframe": timeframe, "leverage": lev, "size_pct": size, "commission_bps": comm,
    }
    if sl:
        pretty["sl_pct"] = sl
    if tp:
        pretty["tp_pct"] = tp
    metrics = {
        "net_pnl_pct": s.get("net_pnl_pct"), "sharpe": s.get("sharpe"),
        "max_drawdown": s.get("max_drawdown"), "win_rate": s.get("win_rate"),
        "trades": s.get("total_trades"),
    }
    pstr = " ".join(f"{k}={v:g}" for k, v in params.items())
    label = f"{symbol} · {strat} {pstr} · {direction}".strip()
    return label, pretty, metrics, out


def _lab_system(context: dict) -> str:
    strats = "\n".join(
        f"- {k} ({t.label}): " + (", ".join(f"{p.key} [{p.min:g}-{p.max:g}]" for p in t.params) or "no params")
        for k, t in lab.TEMPLATES.items()
    )
    sym = context.get("symbol", "AAPL")
    return (
        "You are a quantitative backtesting agent for SINGLE-SYMBOL signal strategies. Each turn "
        "emit ONE JSON object:\n"
        '{"thought":"<short>","tool":"run","args":{"strategy":<key>,"params":{<name>:<value>},'
        '"direction":"long_only"|"short_only"|"both","sl_pct":0-1,"tp_pct":0-1,"leverage":1-5,'
        '"size_pct":0.1-1,"symbol":<optional ticker>,"timeframe":"1d"|"1h","years":1-10}}\n'
        'or finish: {"thought":"...","done":true,"message":"<summary>","best_id":"<run id like r2>"}\n\n'
        f"Default symbol is {sym} (the symbol the user is viewing); set args.symbol only to change it.\n"
        f"Strategies and their tunable params (with ranges):\n{strats}\n\n"
        "sl_pct/tp_pct are FRACTIONS (0.02 = 2%). Policy: resolve the request into EXACTLY ONE "
        "configuration, run it once, then finish with done (best_id = that run). Do NOT sweep or try "
        "multiple strategies/params — even if the request says 'find the best' or 'compare', pick the "
        "single most appropriate config and run only that one. After the run you get an OBSERVATION "
        "with the run id + metrics. Reply with ONLY the JSON action."
    )


# ── portfolio engine ──────────────────────────────────────────────────────────────
# Construct a portfolio from ONE existing source (a factor, an engine strategy, or a saved
# model bundle): run its backtest, turn the resulting target weights into a saved portfolio,
# and value it into shares. Reuses run_backtest / run_engine_strategy / portfolio_book.

def _asset_map(universe_name: str) -> dict[str, str]:
    """symbol -> asset_class for the universe, so weights become correctly-typed allocations."""
    try:
        uni = get_universe(universe_name)
    except Exception:  # noqa: BLE001 - a bad universe just defaults everything to equity
        return {}
    return {ins.id.upper(): ins.asset_class.value for ins in uni.instruments}


def _resolve_source(args: dict, deps: BTDeps) -> dict:
    """Normalize the chosen source into {source, factor|strategy, universe, mode, top_pct, years}."""
    universes = list_universes() or ["dow30"]
    source = args.get("source")
    if source not in _SOURCES:
        source = "model" if args.get("model") else ("strategy" if args.get("strategy") else "factor")

    factor = strategy = None
    universe = args.get("universe")
    mode = args.get("mode")
    top_pct = args.get("top_pct")

    if source == "model":
        name = args.get("model", "")
        models = {m["name"]: m for m in reg.list_models(deps.store).get("models", [])}
        if name not in models:
            raise ValueError(f"unknown model '{name}'")
        b = models[name]
        factor = b.get("factor") or None
        strategy = b.get("strategy") or None if not factor else None
        universe = universe or b.get("universe") or "dow30"
        mode = mode or b.get("mode") or "long_short"
        if not factor and not strategy:
            raise ValueError(f"model '{name}' bundles neither a factor nor a strategy")
        ident = name
    elif source == "strategy":
        strategy = args.get("strategy", "")
        ident = strategy
    else:  # factor
        factor = args.get("factor", "momentum")
        if factor not in fac.CATALOG:
            factor = "momentum"
        ident = factor

    universe = universe if universe in universes else ("dow30" if "dow30" in universes else universes[0])
    if mode not in _MODES:
        mode = "long_short"
    top_pct = _clamp(top_pct if top_pct is not None else 0.2, 0.05, 0.5, 0.2)
    years = int(_clamp(args.get("years", 3), 1, 10, 3))
    return {
        "source": source, "ident": ident, "factor": factor, "strategy": strategy,
        "universe": universe, "mode": mode, "top_pct": top_pct, "years": years,
    }


def _portfolio_run(args: dict, deps: BTDeps) -> tuple[str, dict, dict, dict]:
    spec = _resolve_source(args, deps)
    universe, mode, years = spec["universe"], spec["mode"], spec["years"]
    capital = _clamp(args.get("capital", 1_000_000), 1_000, 1_000_000_000, 1_000_000)

    # 1) backtest the source → standard dashboard payload (carries top_weights)
    if spec["factor"]:
        out = bt.run_backtest(
            deps.dm, deps.fstore, deps.fprov, universe, spec["factor"], mode,
            spec["top_pct"], years, 100_000.0, True,
        )
    else:
        out = eng.run_engine_strategy(
            deps.dm, deps.store, strategy_key=spec["strategy"],
            universe_name=universe, years=years, mode=mode,
        )
    if "error" in out:
        raise ValueError(out["error"])

    # 2) last-rebalance book → typed allocation list (fractions)
    amap = _asset_map(universe)
    raw_alloc = [
        {"symbol": w["symbol"], "asset": amap.get(str(w["symbol"]).upper(), "equity"), "weight": w["weight"] / 100.0}
        for w in out.get("top_weights", [])
    ]
    if not raw_alloc:
        raise ValueError("backtest produced no holdings to build a portfolio from")
    norm = pb.normalize(raw_alloc, mode)
    allocations = norm["allocations"]

    # 3) persist into the Portfolios module (save by default)
    name = (args.get("save_as") or f"{spec['ident']} {universe} {mode}").strip()
    pb.save_portfolio(deps.store, name, {
        "description": f"Constructed from {spec['source']} '{spec['ident']}' on {universe}",
        "mode": mode, "allocations": allocations,
        "tags": [spec["source"], spec["ident"]],
        "notes": f"Auto-built by the portfolio agent ({years}y backtest).",
    })

    # 4) value into shares + attach to the dashboard payload
    alloc_value = pb.allocate(deps.dm, allocations, capital)
    out["portfolio"] = {
        "name": name, "saved": True, "mode": mode, "source": spec["source"], "ident": spec["ident"],
        "allocations": allocations, "exposures": norm["exposures"],
    }
    out["allocation"] = alloc_value

    m = out.get("metrics", {})
    metrics = {
        "sharpe": m.get("sharpe"), "cagr": m.get("cagr"), "max_drawdown": m.get("max_drawdown"),
        "n_holdings": len(allocations),
    }
    params = {
        "source": spec["source"], "universe": universe, "mode": mode, "years": years,
        "capital": capital, "save_as": name,
    }
    if spec["factor"]:
        params["factor"] = spec["factor"]
        params["top_pct"] = spec["top_pct"]
    if spec["strategy"]:
        params["strategy"] = spec["strategy"]
    label = f"portfolio · {spec['source']}:{spec['ident']} · {universe} · {years}y"
    return label, params, metrics, out


def _portfolio_system(deps: BTDeps) -> str:
    factors = ", ".join(fac.CATALOG.keys())
    try:
        strategies = ", ".join(s["name"] for s in eng.list_engine_strategies(deps.store)) or "(none)"
    except Exception:  # noqa: BLE001 - registry hiccup shouldn't break the prompt
        strategies = "(none)"
    try:
        models = ", ".join(m["name"] for m in reg.list_models(deps.store).get("models", [])) or "(none saved)"
    except Exception:  # noqa: BLE001
        models = "(none saved)"
    universes = ", ".join(list_universes() or ["dow30"])
    return (
        "You are a portfolio-construction agent. From ONE existing source — a factor, an engine "
        "strategy, or a saved model bundle — you build a target portfolio, back-test it, save it, and "
        "value it. Each turn emit ONE JSON object:\n"
        '{"thought":"<short>","tool":"run","args":{"source":"factor"|"strategy"|"model",'
        '"factor":<key>,"strategy":<key>,"model":<name>,"universe":<name>,'
        '"mode":"long_only"|"long_short","top_pct":0.05-0.5,"years":1-10,"capital":<number>,'
        '"save_as":<portfolio name, optional>}}\n'
        'or finish: {"thought":"...","done":true,"message":"<summary>","best_id":"<run id like r1>"}\n\n'
        f"Factors: {factors}.\nStrategies: {strategies}.\nModels: {models}.\nUniverses: {universes}.\n\n"
        "Pick the source matching the request: name a `model` to use a saved bundle (it carries its own "
        "factor/universe/mode), else a `factor` (set top_pct) or `strategy`. `top_pct` applies to factors "
        "only. Policy: resolve the request into EXACTLY ONE configuration, run it once, then finish with "
        "done (best_id = that run). After the run you get an OBSERVATION with the run id + metrics. Reply "
        "with ONLY the JSON action."
    )


# ── loop ─────────────────────────────────────────────────────────────────────────
def _build_user(engine: str, goal: str, context: dict, history: list[tuple[str, str]]) -> str:
    lines = [f"User request: {goal}"]
    if engine == "lab":
        lines.append(f"Current symbol: {context.get('symbol', 'AAPL')} ({context.get('timeframe', '1d')})")
    if history:
        lines.append("\nRuns so far:")
        for act, obs in history[-8:]:
            lines.append(f"ACTION: {act}")
            lines.append(f"OBSERVATION: {obs}")
    lines.append("\nRespond with the next action as a single JSON object.")
    return "\n".join(lines)


async def arun_backtest_agent(
    engine: str, goal: str, context: dict | None, deps: BTDeps
) -> AsyncIterator[dict]:
    context = context or {}
    if engine == "factor":
        system = _factor_system()
    elif engine == "portfolio":
        system = _portfolio_system(deps)
    else:
        system = _lab_system(context)
    history: list[tuple[str, str]] = []
    runs = 0
    rid = 0

    for _ in range(_MAX_STEPS):
        user = _build_user(engine, goal, context, history)
        raw = await asyncio.to_thread(
            functools.partial(deps.llm.complete, system, user, model=deps.model, temperature=0.2)
        )
        act = _strip_to_json(raw)
        if not isinstance(act, dict):
            history.append(("(unparseable)", "Reply was not a JSON object. Send one JSON action."))
            continue

        thought = str(act.get("thought", ""))[:300]
        if act.get("done"):
            best = act.get("best_id")
            yield {"type": "done", "message": str(act.get("message", "Done."))[:1000], "best_id": best}
            return
        if act.get("tool") != "run":
            if thought:
                yield {"type": "thought", "text": thought}
            history.append((json.dumps(act)[:200], "Unknown tool — use 'run' or 'done'."))
            continue

        if thought:
            yield {"type": "thought", "text": thought}
        # Single backtest per prompt: once one has succeeded, stop (don't sweep).
        if runs >= _MAX_RUNS:
            yield {"type": "done", "message": "Done — one backtest per request.", "best_id": f"r{rid}" if rid else None}
            return

        args = act.get("args") or {}
        try:
            if engine == "factor":
                label, params, metrics, result = await asyncio.to_thread(_factor_run, args, deps)
            elif engine == "portfolio":
                label, params, metrics, result = await asyncio.to_thread(_portfolio_run, args, deps)
            else:
                label, params, metrics, result = await asyncio.to_thread(_lab_run, args, deps, context)
        except Exception as e:  # noqa: BLE001 - a failed run shouldn't kill the loop (model can retry)
            obs = f"run failed: {type(e).__name__}: {e}"
            yield {"type": "obs", "text": obs, "ok": False}
            history.append((json.dumps(act)[:200], obs))
            continue

        runs += 1  # count only successful runs
        rid += 1
        run_id = f"r{rid}"
        yield {"type": "run", "id": run_id, "label": label, "params": params, "metrics": metrics, "result": result}
        obs = f"run {run_id}: {label} -> " + ", ".join(f"{k} {v}" for k, v in metrics.items() if v is not None)
        history.append((json.dumps(act)[:200], obs))

    yield {"type": "done", "message": f"Stopped after {_MAX_STEPS} steps — showing the best so far.", "best_id": None}
