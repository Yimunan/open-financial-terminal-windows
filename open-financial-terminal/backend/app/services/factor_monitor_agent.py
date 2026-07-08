"""Autonomous factor-performance agent: drives the factor module from a chat goal.

A bounded ReAct loop (same shape as `backtest_agent`): each turn the local LLM emits ONE JSON
action — rank the leaderboard, drill into a factor, or manage a saved monitor — and we validate /
clamp the args to the live enums + ranges (like `assistant.nl_to_screen`), execute it via the
existing `services/factor_monitor` functions, and stream the result back. Multi-step requests
("rank, then drill the top factor") resolve across several turns; the loop is bounded by
`_MAX_STEPS` / `_MAX_RESULTS`.

Frames streamed to the UI:
  {"type":"thought","text"}                                   — the model's short reasoning
  {"type":"result","id","kind","label","params","data"}       — a board / detail / history payload
  {"type":"obs","text","ok"}                                   — save/delete acknowledgement or a failure
  {"type":"done","message","best_id"}                          — final summary, names the result to show
"""

from __future__ import annotations

import asyncio
import functools
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.services import factor_monitor as fm
from app.services import factors as fac
from app.services.agent_assistant import _strip_to_json
from app.services.universe import list_universes

_MAX_STEPS = 6      # total LLM turns
_MAX_RESULTS = 4    # board/detail/history payloads produced per request


@dataclass
class FMDeps:
    dm: Any
    fstore: Any
    fprov: Any
    llm: Any
    model: str
    store: Any = None  # TerminalStore — saved monitors + snapshot history


def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return int(min(hi, max(lo, float(v))))
    except (TypeError, ValueError):
        return default


def _universe(args: dict) -> str:
    universes = list_universes() or ["dow30"]
    u = args.get("universe") or "dow30"
    return u if u in universes else ("dow30" if "dow30" in universes else universes[0])


# ── result-producing tools ─────────────────────────────────────────────────────────
def _rank(args: dict, deps: FMDeps) -> tuple[str, str, dict, dict]:
    universe = _universe(args)
    horizon = _clamp(args.get("horizon", 5), 1, 63, 5)
    q = _clamp(args.get("q", 5), 3, 10, 5)
    factors = args.get("factors") or None  # optional explicit set (built-in / custom / engine names)
    board = fm.scorecard(deps.dm, deps.fstore, deps.fprov, universe, factors, horizon, q, store=deps.store)
    return "board", f"Leaderboard · {universe} · h{horizon} · q{q}", {"universe": universe, "horizon": horizon, "q": q}, board


def _drill(args: dict, deps: FMDeps) -> tuple[str, str, dict, dict]:
    factor = args.get("factor", "momentum")
    if factor not in fm.known_factor_keys(deps.store):  # catalog ∪ custom ∪ engine/linked
        factor = "momentum"
    universe = _universe(args)
    horizon = _clamp(args.get("horizon", 5), 1, 63, 5)
    q = _clamp(args.get("q", 5), 3, 10, 5)
    roll = _clamp(args.get("roll_window", 63), 5, 252, 63)
    detail = fm.factor_detail(deps.dm, deps.fstore, deps.fprov, universe, factor, horizon, q, roll_window=roll, store=deps.store)
    params = {"factor": factor, "universe": universe, "horizon": horizon, "q": q, "roll_window": roll}
    return "detail", f"{factor} · {universe} · h{horizon}", params, detail


def _run_monitor(args: dict, deps: FMDeps) -> tuple[str, str, dict, dict]:
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("a monitor name is required to run")
    board = fm.run_monitor(deps.dm, deps.fstore, deps.fprov, deps.store, name)
    return "board", f"Monitor · {name}", {"name": name}, board


def _history(args: dict, deps: FMDeps) -> tuple[str, str, dict, dict]:
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("a monitor name is required for history")
    hist = fm.monitor_history(deps.store, name)
    return "history", f"History · {name}", {"name": name}, hist


def _heatmap(args: dict, deps: FMDeps) -> tuple[str, str, dict, dict]:
    universe = _universe(args)
    factors = args.get("factors") or None
    data = fm.correlation_matrix(deps.dm, deps.fstore, deps.fprov, universe, factors, store=deps.store)
    return "heatmap", f"Correlation heatmap · {universe}", {"universe": universe}, data


# ── observation-only tools (no dashboard payload) ──────────────────────────────────
def _save(args: dict, deps: FMDeps) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("a monitor name is required to save")
    universe = _universe(args)
    horizon = _clamp(args.get("horizon", 5), 1, 63, 5)
    q = _clamp(args.get("q", 5), 3, 10, 5)
    fm.save_monitor(deps.store, name, {"universe": universe, "factors": [], "horizon": horizon, "q": q})
    return f"saved monitor '{name}' ({universe}, h{horizon}, q{q})"


def _delete(args: dict, deps: FMDeps) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        raise ValueError("a monitor name is required to delete")
    fm.remove_monitor(deps.store, name)
    return f"deleted monitor '{name}'"


def _list_factors(args: dict, deps: FMDeps) -> str:
    """List every factor the module can evaluate, grouped by source (the factor directory)."""
    builtin = ", ".join(fac.CATALOG)
    custom = ", ".join(fm._custom_index(deps.store)) or "(none)"
    engine = [n for n in fm._engine_names(deps.store) if n not in fac.CATALOG]
    engine_str = ", ".join(engine) or "(none)"
    return f"built-in: {builtin} | custom: {custom} | engine/linked: {engine_str}"


_RESULT_TOOLS = {"rank": _rank, "drill": _drill, "run": _run_monitor, "history": _history, "heatmap": _heatmap}
_OBS_TOOLS = {"save": _save, "delete": _delete, "factors": _list_factors}


def _observe(kind: str, run_id: str, label: str, data: dict) -> str:
    if kind == "board":
        top = ", ".join(f"{r['factor']} (ic_ir {r['ic_ir']})" for r in data.get("rows", [])[:5])
        return f"result {run_id}: {label}; top factors by IC-IR: {top}"
    if kind == "detail":
        m = data.get("metrics", {})
        ret = data.get("returns", {})
        risk = data.get("risk", {})
        return (
            f"result {run_id}: {label}; mean_ic {m.get('mean_ic')}, ic_ir {m.get('ic_ir')}, "
            f"hit% {m.get('hit_rate')}, LS total return {ret.get('ls_total_return')}%, "
            f"beta {risk.get('beta')}, market_corr {risk.get('market_corr')}"
        )
    if kind == "history":
        return f"result {run_id}: {label}; {data.get('n_snapshots')} snapshot(s)"
    if kind == "heatmap":
        return f"result {run_id}: {label}; {len(data.get('factors', []))} factors correlated"
    return f"result {run_id}: {label}"


def _system(deps: FMDeps, context: dict) -> str:
    builtin = ", ".join(f"{k} ({v['label']})" for k, v in fac.CATALOG.items())
    custom = ", ".join(fm._custom_index(deps.store)) or "(none)"
    engine = [n for n in fm._engine_names(deps.store) if n not in fac.CATALOG]
    engine_str = ", ".join(engine[:30]) or "(none)"
    universes = ", ".join(list_universes() or ["dow30"])
    try:
        monitors = ", ".join(m["name"] for m in fm.list_monitors(deps.store).get("monitors", [])) or "(none saved)"
    except Exception:  # noqa: BLE001 - a store hiccup shouldn't break the prompt
        monitors = "(none saved)"
    return (
        "You are a factor-performance research agent for a quant terminal. Each turn emit ONE JSON object:\n"
        '{"thought":"<short>","tool":"rank","args":{"universe":<name>,"horizon":1-63,"q":3-10,"factors":[<keys, optional>]}}\n'
        '{"thought":"...","tool":"drill","args":{"factor":<key>,"universe":<name>,"horizon":1-63,"q":3-10,"roll_window":5-252}}\n'
        '{"thought":"...","tool":"heatmap","args":{"universe":<name>,"factors":[<keys, optional>]}}\n'
        '{"thought":"...","tool":"factors","args":{}}\n'
        '{"thought":"...","tool":"save","args":{"name":<monitor name>,"universe":<name>,"horizon":1-63,"q":3-10}}\n'
        '{"thought":"...","tool":"run","args":{"name":<saved monitor>}}\n'
        '{"thought":"...","tool":"history","args":{"name":<saved monitor>}}\n'
        '{"thought":"...","tool":"delete","args":{"name":<saved monitor>}}\n'
        'or finish: {"thought":"...","done":true,"message":"<summary>","best_id":"<result id like r2>"}\n\n'
        "rank ranks factors over a universe by information coefficient (the leaderboard); by default it "
        "includes the built-in trial factors AND the user's custom factors. "
        "drill deep-dives ONE factor across three layers — Returns (long-short decile curve, drawdown, "
        "quantile monotonicity), Risk (correlation to other factors, beta/market-correlation vs SPY), and "
        "Health (rolling IC, decay, turnover, regime breakdown). "
        "heatmap shows the full factor-vs-factor correlation matrix over a universe. "
        "factors lists every factor you can analyze (built-in, custom, engine/linked). "
        "drill/rank/heatmap accept custom and engine factor names, not just built-ins. "
        "save/run/history/delete manage saved monitors.\n"
        f"Built-in factors: {builtin}.\nCustom factors: {custom}.\nEngine/linked factors: {engine_str}.\n"
        f"Universes: {universes}.\nSaved monitors: {monitors}.\n"
        f"Current state: {json.dumps(context)}.\n\n"
        "Policy: carry out the request in as few steps as possible, then finish with done. For "
        "'rank then drill the best', rank first, read the OBSERVATION's top factor, drill into it, "
        "then done (best_id = the drill result). Resolve relative requests like 'now on sp500' against "
        "the current state. After each tool you get an OBSERVATION. Reply with ONLY the JSON action."
    )


def _build_user(goal: str, context: dict, history: list[tuple[str, str]]) -> str:
    lines = [f"User request: {goal}"]
    if context:
        lines.append(f"Current state: {json.dumps(context)}")
    if history:
        lines.append("\nSteps so far:")
        for act, obs in history[-8:]:
            lines.append(f"ACTION: {act}")
            lines.append(f"OBSERVATION: {obs}")
    lines.append("\nRespond with the next action as a single JSON object.")
    return "\n".join(lines)


async def arun_factor_monitor_agent(
    goal: str, context: dict | None, deps: FMDeps
) -> AsyncIterator[dict]:
    context = context or {}
    system = _system(deps, context)
    history: list[tuple[str, str]] = []
    results = 0
    rid = 0

    for _ in range(_MAX_STEPS):
        user = _build_user(goal, context, history)
        raw = await asyncio.to_thread(
            functools.partial(deps.llm.complete, system, user, model=deps.model, temperature=0.2)
        )
        act = _strip_to_json(raw)
        if not isinstance(act, dict):
            history.append(("(unparseable)", "Reply was not a JSON object. Send one JSON action."))
            continue

        thought = str(act.get("thought", ""))[:300]
        if act.get("done"):
            yield {"type": "done", "message": str(act.get("message", "Done."))[:1000], "best_id": act.get("best_id")}
            return

        tool = act.get("tool")
        if thought:
            yield {"type": "thought", "text": thought}

        if tool in _OBS_TOOLS:
            try:
                obs = await asyncio.to_thread(_OBS_TOOLS[tool], act.get("args") or {}, deps)
                yield {"type": "obs", "text": obs, "ok": True}
            except Exception as e:  # noqa: BLE001 - a failed action shouldn't kill the loop (model can retry)
                obs = f"{tool} failed: {type(e).__name__}: {e}"
                yield {"type": "obs", "text": obs, "ok": False}
            history.append((json.dumps(act)[:200], obs))
            continue

        if tool not in _RESULT_TOOLS:
            history.append((json.dumps(act)[:200], "Unknown tool — use rank/drill/save/run/history/delete or done."))
            continue

        if results >= _MAX_RESULTS:
            yield {"type": "done", "message": "Done — result limit reached.", "best_id": f"r{rid}" if rid else None}
            return

        try:
            kind, label, params, data = await asyncio.to_thread(_RESULT_TOOLS[tool], act.get("args") or {}, deps)
        except Exception as e:  # noqa: BLE001 - a failed run shouldn't kill the loop
            obs = f"{tool} failed: {type(e).__name__}: {e}"
            yield {"type": "obs", "text": obs, "ok": False}
            history.append((json.dumps(act)[:200], obs))
            continue

        results += 1
        rid += 1
        run_id = f"r{rid}"
        yield {"type": "result", "id": run_id, "kind": kind, "label": label, "params": params, "data": data}
        history.append((json.dumps(act)[:200], _observe(kind, run_id, label, data)))

    yield {"type": "done", "message": f"Stopped after {_MAX_STEPS} steps — showing the latest result.", "best_id": f"r{rid}" if rid else None}
