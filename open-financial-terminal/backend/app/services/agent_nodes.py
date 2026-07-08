"""Agent-workflow node registry: the palette of step types and how each one runs.

Each node type maps to a fixed Python body that calls an existing terminal service or the
local LLM. Only the node's CONFIG (params / prompts) is user-set — no user code is executed.
`run_node` is the single dispatch point used by the dynamic LangGraph (`agent_graph.py`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services import agent_code as ac
from app.services import backtest as bt
from app.services import fundamentals as fa
from app.services import market as mkt
from app.services import news_router as nr
from app.services import scenario as sc
from app.services import screener as scr
from app.services.broker import SimBroker


@dataclass
class AgentDeps:
    dm: Any
    fstore: Any
    fprov: Any
    llm: Any
    model: str
    llm_base_url: str
    broker: Any = None
    store: Any = None  # terminal SQLite — resolves saved committee rosters
    committee_base_url: str = "http://localhost:8083"  # crewai-service (Investment Committee crew)


# ── palette schema (drives the frontend node editor) ────────────────────────────
# param types: text | number | select | textarea. `inputs` = number of input ports.
NODE_TYPES: list[dict] = [
    {
        "key": "input", "label": "Input", "category": "io", "inputs": 0,
        "params": [
            {"key": "symbol", "label": "Symbol", "type": "text", "default": "AAPL"},
            {"key": "asset", "label": "Asset", "type": "select", "default": "equity",
             "options": ["equity", "crypto"]},
        ],
    },
    {
        "key": "scenario", "label": "Scenario", "category": "io", "inputs": 0,
        "params": [
            {"key": "universe", "label": "Universe", "type": "text", "default": "dow30"},
            {"key": "factor", "label": "Factor", "type": "text", "default": "momentum"},
            {"key": "mode", "label": "Mode", "type": "select", "default": "long_only",
             "options": ["long_only", "long_short"]},
            {"key": "top_pct", "label": "Top fraction", "type": "number", "default": 0.2},
            {"key": "years", "label": "Years", "type": "number", "default": 3},
            {"key": "initial", "label": "Capital $", "type": "number", "default": 100000},
            {"key": "equity_pct", "label": "Equity shock %", "type": "number", "default": 0},
            {"key": "crypto_pct", "label": "Crypto shock %", "type": "number", "default": 0},
            {"key": "vol_mult", "label": "Vol ×", "type": "number", "default": 1},
        ],
    },
    {
        "key": "scenario_ref", "label": "Saved Scenario", "category": "io", "inputs": 0,
        "params": [
            {"key": "name", "label": "Scenario name", "type": "text", "default": ""},
        ],
    },
    {
        "key": "data", "label": "Data", "category": "pipeline", "inputs": 1,
        "params": [
            {"key": "universe", "label": "Universe", "type": "text", "default": "dow30"},
            {"key": "symbol", "label": "Symbol (optional)", "type": "text", "default": ""},
        ],
    },
    {
        "key": "strategy", "label": "Strategy", "category": "pipeline", "inputs": 1,
        "params": [
            {"key": "factor", "label": "Factor", "type": "text", "default": "momentum"},
            {"key": "mode", "label": "Mode", "type": "select", "default": "long_only",
             "options": ["long_only", "long_short"]},
            {"key": "top_pct", "label": "Top fraction", "type": "number", "default": 0.2},
            {"key": "years", "label": "Years", "type": "number", "default": 3},
        ],
    },
    {
        "key": "portfolio", "label": "Portfolio", "category": "pipeline", "inputs": 1,
        "params": [{"key": "initial", "label": "Capital $", "type": "number", "default": 100000}],
    },
    {
        "key": "execution", "label": "Execution (paper)", "category": "pipeline", "inputs": 1,
        "params": [
            {"key": "top_n", "label": "Top N to buy", "type": "number", "default": 5},
            {"key": "arm", "label": "Arm (place orders)", "type": "select", "default": "off",
             "options": ["off", "on"]},
        ],
    },
    {
        "key": "quote", "label": "Quote", "category": "data", "inputs": 1,
        "params": [{"key": "symbol", "label": "Symbol (or from input)", "type": "text", "default": ""}],
    },
    {
        "key": "fundamentals", "label": "Fundamentals", "category": "data", "inputs": 1,
        "params": [{"key": "symbol", "label": "Symbol (or from input)", "type": "text", "default": ""}],
    },
    {
        "key": "news", "label": "News + sentiment", "category": "data", "inputs": 1,
        "params": [
            {"key": "symbol", "label": "Symbol (or from input)", "type": "text", "default": ""},
            {"key": "limit", "label": "Headlines", "type": "number", "default": 8},
        ],
    },
    {
        "key": "screen", "label": "Factor screen", "category": "data", "inputs": 1,
        "params": [
            {"key": "universe", "label": "Universe", "type": "text", "default": "dow30"},
            {"key": "factor", "label": "Factor", "type": "text", "default": "momentum"},
            {"key": "limit", "label": "Top N", "type": "number", "default": 10},
        ],
    },
    {
        "key": "backtest", "label": "Backtest", "category": "data", "inputs": 1,
        "params": [
            {"key": "universe", "label": "Universe", "type": "text", "default": "dow30"},
            {"key": "factor", "label": "Factor", "type": "text", "default": "momentum"},
            {"key": "mode", "label": "Mode", "type": "select", "default": "long_only",
             "options": ["long_only", "long_short"]},
            {"key": "years", "label": "Years", "type": "number", "default": 3},
        ],
    },
    {
        "key": "llm", "label": "LLM", "category": "llm", "inputs": 1,
        "params": [
            {"key": "prompt", "label": "Prompt", "type": "textarea",
             "default": "Summarize the following for an investor:\n\n{input}"},
            {"key": "temperature", "label": "Temperature", "type": "number", "default": 0.4},
        ],
    },
    {
        "key": "research", "label": "Research Loop", "category": "llm", "inputs": 1,
        "params": [
            {"key": "goal", "label": "Goal", "type": "textarea",
             "default": "Find a robust long-short factor strategy that passes the promotion scorecard"},
            {"key": "max_iters", "label": "Iterations (≤15)", "type": "number", "default": 3},
            {"key": "target_sharpe", "label": "Target Sharpe (0=off)", "type": "number", "default": 0},
            {"key": "committee", "label": "Committee in loop", "type": "select", "default": "off",
             "options": ["off", "on"]},
            {"key": "build_portfolio", "label": "Build portfolio", "type": "select", "default": "off",
             "options": ["off", "on"]},
        ],
    },
    {
        "key": "committee", "label": "Committee", "category": "llm", "inputs": 1,
        "params": [
            # `options` are populated live from the Committee module (presets + saved templates)
            # by the /node-types endpoint; these are just the built-in fallback.
            {"key": "committee", "label": "Committee", "type": "select",
             "default": "Investment Committee", "options": ["Investment Committee", "Risk Committee"]},
            {"key": "prompt", "label": "Mandate", "type": "textarea",
             "default": "Deliberate and issue a verdict using the analysis below.\n\n{input}"},
        ],
    },
    {
        "key": "code", "label": "Python step", "category": "code", "inputs": 1,
        "params": [
            {"key": "code", "label": "Python", "type": "code", "default": ac.STARTER_CODE},
        ],
    },
    {
        "key": "output", "label": "Output", "category": "io", "inputs": 1, "params": [],
    },
]

_BY_KEY = {n["key"]: n for n in NODE_TYPES}


def node_types() -> list[dict]:
    return NODE_TYPES


# ── execution ────────────────────────────────────────────────────────────────────
def _cfg(node: dict, key: str, default: Any = None) -> Any:
    return (node.get("config") or {}).get(key, default)


def _symbol(node: dict, inputs: dict[str, dict]) -> tuple[str, str]:
    """Resolve a symbol+asset: the node's own param wins, else an upstream input's value."""
    sym = (_cfg(node, "symbol") or "").strip()
    asset = "equity"
    for res in inputs.values():
        v = res.get("value")
        if isinstance(v, dict) and v.get("symbol"):
            if not sym:
                sym = v["symbol"]
            asset = v.get("asset", asset)
    return (sym or "AAPL").upper(), asset


def _fill(template: str, inputs: dict[str, dict]) -> str:
    """Fill {nodeId} placeholders with that source's summary; {input} = all inputs joined."""
    joined = "\n\n".join(f"[{sid}]\n{res.get('summary', '')}" for sid, res in inputs.items())
    out = template.replace("{input}", joined)
    for sid, res in inputs.items():
        out = re.sub(r"\{" + re.escape(sid) + r"\}", str(res.get("summary", "")), out)
    return out


def _committee_roster(deps: AgentDeps, name: str) -> list[dict]:
    """Resolve a committee name to its roster: saved template first, then a built-in preset.

    Returns ``[]`` when neither matches — the crewai committee crew then falls back to its own
    DEFAULT_ROSTER, so the default "Investment Committee" still works with no setup.
    """
    if deps.store is not None:
        rec = deps.store.get_committee_template(name)
        if rec and rec.get("members"):
            return rec["members"]
    from app.routers.committee import PRESETS  # deferred: avoid service→router import at load

    for p in PRESETS:
        if p["name"].lower() == name.lower():
            return p["members"]
    return []


def _parse_verdict(text: str) -> dict | None:
    """Pull the Chair's fenced ```json``` verdict block out of the committee's markdown."""
    import json

    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def _spec(inputs: dict[str, dict]) -> dict:
    """Merge upstream dict values — the quant pipeline accumulates a strategy spec (+ shocks)."""
    s: dict = {}
    for res in inputs.values():
        v = res.get("value")
        if isinstance(v, dict):
            s.update({k: val for k, val in v.items() if k in _SPEC_KEYS})
            if isinstance(v.get("shocks"), dict):
                s["shocks"] = v["shocks"]
    return s


_SPEC_KEYS = {"universe", "symbol", "asset", "factor", "mode", "top_pct", "years", "initial"}


def _ctx(context: dict, spec: dict, node: dict, key: str, default: Any = None) -> Any:
    """Resolve a value: active scenario context wins, then an upstream spec, then node config.

    The scenario (run preset) overriding node configs is what lets one graph run under many
    presets. ``spec`` is the accumulated upstream pipeline dict; ``node`` its own config.
    """
    if context.get(key) not in (None, ""):
        return context[key]
    if spec.get(key) not in (None, ""):
        return spec[key]
    return _cfg(node, key, default)


def _shocks(context: dict, spec: dict) -> dict:
    return sc.clean_shocks(context.get("shocks") or spec.get("shocks"))


def run_node(node: dict, inputs: dict[str, dict], deps: AgentDeps, context: dict | None = None,
             on_progress: Callable[[dict], None] | None = None) -> dict:
    """Run one node. Returns {value, summary} (both JSON-serializable).

    ``context`` is the active scenario's variables + shocks; it overrides node configs.
    ``on_progress`` (research node only) is fired with per-iteration research-loop frames so the
    agent graph can stream live progress into the revealed Research Loop module.
    """
    key = node["type"]
    context = context or {}
    if key not in _BY_KEY:
        raise ValueError(f"unknown node type '{key}'")

    if key == "scenario":
        s = {k: _cfg(node, k) for k in ("universe", "factor", "mode") if _cfg(node, k) not in (None, "")}
        s["top_pct"] = float(_cfg(node, "top_pct", 0.2) or 0.2)
        s["years"] = int(_cfg(node, "years", 3) or 3)
        s["initial"] = float(_cfg(node, "initial", 100000) or 100000)
        s["shocks"] = sc.clean_shocks({
            "equity_pct": _cfg(node, "equity_pct", 0), "crypto_pct": _cfg(node, "crypto_pct", 0),
            "vol_mult": _cfg(node, "vol_mult", 1),
        })
        sh = "" if not sc.is_active(s["shocks"]) else \
            f" · shock eq {s['shocks']['equity_pct']}%/cx {s['shocks']['crypto_pct']}%/vol×{s['shocks']['vol_mult']}"
        return {"value": s, "summary": f"scenario: {s.get('factor','?')} on {s.get('universe','?')}{sh}"}

    if key == "scenario_ref":
        name = (_cfg(node, "name", "") or "").strip()
        if not name:
            raise RuntimeError("Saved Scenario node needs a scenario name")
        s = sc.load_spec(deps.store, name) if deps.store is not None else None
        if s is None:
            raise RuntimeError(f"no saved scenario named '{name}'")
        shocks = s.get("shocks", {})
        sh = "" if not sc.is_active(shocks) else \
            f" · shock eq {shocks['equity_pct']}%/cx {shocks['crypto_pct']}%/vol×{shocks['vol_mult']}"
        vars_desc = ", ".join(f"{k}={v}" for k, v in s.items() if k != "shocks") or "no variables"
        return {"value": s, "summary": f"scenario '{name}': {vars_desc}{sh}"}

    # ── quant pipeline: data → strategy → portfolio → {backtest, execution} ──
    if key == "data":
        s = _spec(inputs)
        s["universe"] = _ctx(context, s, node, "universe", "dow30")
        sym = (context.get("symbol") or _cfg(node, "symbol") or "").strip()
        if sym:
            s["symbol"] = sym.upper()
        return {"value": s, "summary": f"universe: {s['universe']}" + (f" · {s['symbol']}" if sym else "")}

    if key == "strategy":
        s = _spec(inputs)
        s["factor"] = _ctx(context, s, node, "factor", "momentum")
        s["mode"] = _ctx(context, s, node, "mode", "long_only")
        s["top_pct"] = float(_ctx(context, s, node, "top_pct", 0.2) or 0.2)
        s["years"] = int(_ctx(context, s, node, "years", 3) or 3)
        return {"value": s, "summary": f"{s['factor']} · {s['mode']} · top {s['top_pct']} on {s.get('universe', 'dow30')}"}

    if key == "portfolio":
        s = _spec(inputs)
        s["initial"] = float(_ctx(context, s, node, "initial", 100000) or 100000)
        return {"value": s, "summary": f"capital ${s['initial']:,.0f} · {s.get('factor', '?')} on {s.get('universe', '?')}"}

    if key == "execution":
        s = _spec(inputs)
        universe = _ctx(context, s, node, "universe", "dow30")
        factor = _ctx(context, s, node, "factor", "momentum")
        top_n = int(_cfg(node, "top_n", 5) or 5)
        arm = _cfg(node, "arm", "off") == "on"
        screen = scr.run_factor_screen(deps.dm, deps.fstore, deps.fprov, universe, factor, top_n)
        picks = screen.get("results", [])[:top_n]
        acct = deps.broker.get_account() if deps.broker else None
        budget = (acct.equity / max(top_n, 1)) if acct else 0.0
        lines = []
        for r in picks:
            px = r.get("price")
            if not px:
                continue
            qty = max(1, int(budget / px)) if budget else 1
            if arm and deps.broker is not None:
                from qhfi.execution.base import Order, OrderSide

                order = Order(instrument_id=r["symbol"], side=OrderSide.BUY, quantity=qty, type="market")
                oid = (deps.broker.submit(order, r.get("asset", "equity"))
                       if isinstance(deps.broker, SimBroker) else deps.broker.submit(order))
                lines.append(f"BUY {qty} {r['symbol']} @~{px} → order #{oid}")
            else:
                lines.append(f"BUY {qty} {r['symbol']} @~{px}  (dry-run)")
        verb = "PLACED" if arm else "PLANNED (set Arm=on to place)"
        return {"value": {"orders": lines, "armed": arm}, "summary": f"Execution {verb}:\n" + "\n".join(lines)}

    if key == "code":
        return ac.run_user_code(_cfg(node, "code", "") or "", inputs, deps)

    if key == "input":
        sym = (context.get("symbol") or _cfg(node, "symbol", "AAPL") or "AAPL").upper()
        asset = context.get("asset") or _cfg(node, "asset", "equity")
        return {"value": {"symbol": sym, "asset": asset}, "summary": f"{sym} ({asset})"}

    if key == "quote":
        sym, asset = _symbol(node, inputs)
        _, bars = mkt.fetch_bars(deps.dm, sym, asset)
        q = mkt.quote_from_bars(bars)
        return {
            "value": {"symbol": sym, "asset": asset, **q},
            "summary": f"{sym}  {q.get('price')}  ({q.get('change_pct')}%)  asof {q.get('asof')}",
        }

    if key == "fundamentals":
        sym, _ = _symbol(node, inputs)
        snap = fa.snapshot(sym)
        lines = [f"{k}: {v}" for k, v in snap.items() if k != "summary" and v is not None]
        summ = f"{sym} fundamentals\n" + "\n".join(lines[:12])
        if snap.get("summary"):
            summ += f"\n\n{snap['summary'][:500]}"
        return {"value": snap, "summary": summ}

    if key == "news":
        sym, _ = _symbol(node, inputs)
        limit = int(_cfg(node, "limit", 8) or 8)
        items = nr.news(sym)[:limit]
        try:
            nr.apply_scores(deps.llm, sym, items)
        except Exception:  # noqa: BLE001 - sentiment is best-effort
            pass
        lines = [f"- [{it.get('sentiment', '?')}] {it['title']} ({it.get('publisher', '')})" for it in items]
        return {"value": items, "summary": f"{sym} news ({len(items)})\n" + "\n".join(lines)}

    if key == "screen":
        out = scr.run_factor_screen(
            deps.dm, deps.fstore, deps.fprov,
            _ctx(context, {}, node, "universe", "dow30"), _ctx(context, {}, node, "factor", "momentum"),
            int(_cfg(node, "limit", 10) or 10),
        )
        rows = out.get("results", [])
        lines = [f"{i + 1}. {r['symbol']}  score {round(r['score'], 3)}" for i, r in enumerate(rows)]
        return {"value": out, "summary": f"{out.get('factor')} · {out.get('universe')}\n" + "\n".join(lines)}

    if key == "backtest":
        s = _spec(inputs)  # consume an upstream strategy/portfolio spec if wired
        out = bt.run_backtest(
            deps.dm, deps.fstore, deps.fprov,
            _ctx(context, s, node, "universe", "dow30"),
            _ctx(context, s, node, "factor", "momentum"),
            _ctx(context, s, node, "mode", "long_only"),
            float(s.get("top_pct") or 0.2),
            int(_ctx(context, s, node, "years", 3) or 3),
            float(s.get("initial") or 100_000.0),
        )
        m = out.get("metrics", {})
        rob = out.get("robustness", {})
        summ = (f"{out.get('factor')} {out.get('mode')} {out.get('universe')}: "
                f"Sharpe {m.get('sharpe')}, CAGR {m.get('cagr')}%, MaxDD {m.get('max_drawdown')}%, "
                f"PSR {rob.get('psr')}, DSR {rob.get('dsr')}")
        # Scenario shock: stress the last book by an instantaneous price move.
        shocks = _shocks(context, s)
        if sc.is_active(shocks) and out.get("top_weights"):
            stress = sc.stress_weights(out["top_weights"], shocks)
            out["stress"] = stress
            summ += (f"\nStress (eq {stress['equity_pct']}% / vol×{stress['vol_mult']}): "
                     f"book P&L {stress['stress_pnl_pct']:+.2f}%")
        return {"value": out, "summary": summ}

    if key == "research":
        # Autonomous research loop: designs a factor experiment, backtests it, grades it against
        # the promotion scorecard, reflects, and redoes up to 5 iterations — returns the best.
        from app.services import research_loop as rl

        goal = (_cfg(node, "goal", "") or
                "Find a strategy that passes the promotion scorecard").strip()
        max_iters = int(_cfg(node, "max_iters", 3) or 3)
        target = float(_cfg(node, "target_sharpe", 0) or 0)
        target_sharpe = target if target > 0 else None
        use_committee = _cfg(node, "committee", "off") == "on"
        build_portfolio = _cfg(node, "build_portfolio", "off") == "on"
        # Upstream summaries (e.g. a committee review) become guidance the loop tries to address.
        guidance = "\n\n".join(
            res.get("summary", "") for res in inputs.values() if res.get("summary")
        ).strip() or None
        rdeps = rl.RLDeps(dm=deps.dm, fstore=deps.fstore, fprov=deps.fprov,
                          llm=deps.llm, model=deps.model, store=deps.store)

        def _on_event(frame: dict) -> None:
            # Stream live per-iteration frames to the revealed Research Loop module. Skip the large
            # `result` payload (per-iteration backtest blob) — it would bloat the panel-param channel;
            # the rail/metrics/phases come from the other (small) frames.
            if on_progress is not None and frame.get("type") != "result":
                on_progress(frame)

        _on_event({"type": "started", "run_id": "agent", "goal": goal})
        out = rl.run_loop_sync(goal, rdeps, max_iters=max_iters, guidance=guidance,
                               target_sharpe=target_sharpe, use_committee=use_committee,
                               build_portfolio=build_portfolio, on_event=_on_event)
        best = out.get("best")
        _on_event({"type": "done", "run_id": "agent", "best_iteration": (best or {}).get("i"),
                   "best": best, "n_iterations": out.get("n_iterations")})
        if best:
            m = best.get("metrics", {})
            lines = [f"Research loop · {out['n_iterations']} iterations",
                     f"Best: {best['label']}",
                     f"checks passed {best['n_checks_passed']} · Sharpe {m.get('sharpe')} · "
                     f"Calmar {m.get('calmar')} · gate {'PASSED' if best['passed'] else 'not passed'}"]
            if target_sharpe is not None:
                status = "MET ✓" if out.get("target_met") else f"not met (best {out.get('best_sharpe')})"
                lines.append(f"Target Sharpe {target_sharpe}: {status}")
            port = out.get("portfolio")
            if isinstance(port, dict) and port.get("name"):
                lines.append(f"Portfolio saved: {port['name']} ({port.get('rebalance')} rebalance, "
                             f"{port.get('timing')} timing)")
            elif isinstance(port, dict) and port.get("error"):
                lines.append(f"Portfolio build failed: {port['error']}")
            summ = "\n".join(lines)
        else:
            summ = f"Research loop ran {out['n_iterations']} iteration(s) — no successful result"
        return {"value": out, "summary": summ}

    if key == "llm":
        prompt = _fill(_cfg(node, "prompt", "{input}"), inputs)
        temp = float(_cfg(node, "temperature", 0.4) or 0.4)
        text = deps.llm.complete(
            "You are a precise financial research assistant.", prompt, model=deps.model, temperature=temp
        )
        return {"value": text, "summary": text}

    if key == "committee":
        import httpx

        name = (_cfg(node, "committee", "Investment Committee") or "Investment Committee").strip()
        sym, _ = _symbol(node, inputs)
        mandate = _fill(_cfg(node, "prompt", "{input}") or "{input}", inputs).strip()
        payload = {
            "crew": "committee",
            "inputs": {
                "prompt": mandate,
                "symbol": sym,
                "committee": name,
                "members": _committee_roster(deps, name),
            },
        }
        base = (deps.committee_base_url or "http://localhost:8083").rstrip("/")
        # Prefer the rich CrewAI deliberation; if it's unreachable/erroring, fall back to a LOCAL
        # LLM committee (same proxy the rest of the stack uses) so the review still happens — only
        # if BOTH fail do we degrade to "unavailable". Degrading rather than raising matters because
        # a node that raises aborts the whole graph (agent_graph re-raises).
        source = "crewai"
        try:
            # Cap the read at 150s: real deliberations finish in ~15-80s, so this preserves them
            # while preventing a degraded CrewAI (its own LLM stalling) from hanging the node — and
            # the whole graph's canvas node — for minutes. On timeout httpx.ReadTimeout (an
            # httpx.HTTPError) is caught below and we fall back to the local LLM committee.
            with httpx.Client(timeout=httpx.Timeout(150.0, connect=10.0)) as client:
                r = client.post(f"{base}/run", json=payload)
            r.raise_for_status()
            result = (r.json().get("result") or "").strip()
        except httpx.HTTPError as e:
            from app.services import committee_local as cl

            try:
                result = (cl.deliberate(deps.llm, deps.model, name,
                                        payload["inputs"]["members"], mandate).get("result") or "").strip()
                source = "local"
            except Exception as le:  # noqa: BLE001 - local LLM also unusable → degrade, don't abort
                return {
                    "value": {"symbol": sym, "committee": name, "verdict": None, "available": False},
                    "summary": (f"{name} review unavailable (CrewAI {type(e).__name__}; "
                                f"local fallback {type(le).__name__}) — proceeding without it"),
                }
        verdict = _parse_verdict(result)
        tag = "" if source == "crewai" else " · local"
        headline = ""
        if verdict:
            headline = (f"{name}{tag} on {sym}: {verdict.get('recommendation', '?')}"
                        f" · conviction {verdict.get('conviction', '?')}\n\n")
        return {
            "value": {"symbol": sym, "committee": name, "verdict": verdict, "text": result,
                      "available": True, "source": source},
            "summary": (headline + result) or f"{name}: no verdict returned",
        }

    # output — pass the single input through
    if inputs:
        first = next(iter(inputs.values()))
        return {"value": first.get("value"), "summary": first.get("summary", "")}
    return {"value": None, "summary": ""}
