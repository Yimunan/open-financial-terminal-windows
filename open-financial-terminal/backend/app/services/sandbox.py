"""Code sandbox — author + run + save module logic for the Factor / Strategy / Portfolio
modules, in two trust modes.

* **sandboxed** — the source is AST-allowlisted (``agent_code._validate``: no imports / dunders /
  eval / open / network) and run in a curated namespace. The terminal is reachable over Tailscale,
  so untrusted edits run here.
* **trusted** — full Python (the same trust model as the drop-in directory loader): the module is
  ``exec``'d as-is and we pick the qhfi ``Factor`` / ``Strategy`` subclass it defines.

Each (mode × trust) reuses an existing run path:
  - strategy/sandboxed → ``strategy_lab.run_lab`` (single-symbol lab) via a temp custom registration
  - strategy/trusted   → ``engine_strategy.run_strategy_instance`` (portfolio backtest)
  - factor/sandboxed   → run the formula per symbol over a universe → cross-sectional ranking
  - factor/trusted     → instantiate the ``Factor`` and take ``signed()`` last cross-section
  - portfolio/*        → eval code → weights → ``portfolio_book.normalize`` + ``allocate``

Save routes to the matching home: sandboxed → the custom registry; trusted → a ``.py`` dropped
into the linked factors/strategies dir (so the drop-in loader registers it); portfolio → the
Portfolios module.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from qhfi.core.types import DateRange
from qhfi.factors.base import Factor
from qhfi.strategy.base import Strategy

from app.services import agent_code as ac
from app.services import engine_strategy as eng
from app.services import market as mkt
from app.services import portfolio_book as pb
from app.services import registry as reg
from app.services import strategy_lab as lab
from app.services.universe import get_universe

MODES = ("factor", "strategy", "portfolio")
TRUSTS = ("sandboxed", "trusted")
_MAX_FACTOR_SYMBOLS = 60  # keep the per-symbol sandboxed sweep responsive


# ── trusted exec helpers ───────────────────────────────────────────────────────────
def _exec_trusted(code: str) -> dict:
    """Full-Python exec (trusted mode only). Returns the module namespace."""
    ns: dict[str, Any] = {"__name__": "oft_sandbox_module"}
    exec(compile(code, "<sandbox-trusted>", "exec"), ns)  # noqa: S102 - trusted mode, by design
    return ns


def _find_subclass(ns: dict, base: type) -> type | None:
    """The class the user defined in this module that subclasses ``base`` (last one wins)."""
    found = None
    for v in ns.values():
        if isinstance(v, type) and v is not base and issubclass(v, base) \
                and getattr(v, "__module__", "") == ns.get("__name__"):
            found = v
    return found


# ── factor ─────────────────────────────────────────────────────────────────────────
def _factor_formula(code: str):
    """Compile a sandboxed factor formula: gets per-symbol OHLCV Series + params, sets `result`."""
    tree = ac._validate(code)

    def fn(bars: pd.DataFrame, params: dict) -> Any:
        ns = {
            "__builtins__": ac._SAFE_BUILTINS, "pd": pd, "np": np, "math": math,
            "close": bars["close"], "high": bars["high"], "low": bars["low"],
            "open": bars["open"], "volume": bars["volume"],
            "params": dict(params), "result": None,
        }
        exec(compile(tree, "<factor>", "exec"), ns)  # noqa: S102 - sandboxed + AST-allowlisted
        return ns.get("result")

    return fn


def _last_value(res: Any) -> float | None:
    s = pd.Series(res).dropna() if res is not None else pd.Series(dtype=float)
    if s.empty:
        return None
    v = float(s.iloc[-1])
    return v if v == v else None


def _run_factor_sandboxed(deps: Any, code: str, ctx: dict) -> dict:
    fn = _factor_formula(code)
    uni = get_universe(ctx.get("universe", "dow30"))
    insts = uni.instruments[:_MAX_FACTOR_SYMBOLS]
    scores: dict[str, float] = {}
    errors = 0
    for ins in insts:
        try:
            _, bars = mkt.fetch_bars(deps.dm, ins.id, ins.asset_class.value)
            if bars.empty:
                continue
            v = _last_value(fn(bars, ctx.get("params", {})))
            if v is not None:
                scores[ins.id] = v
        except Exception:  # noqa: BLE001 - one bad symbol shouldn't sink the sweep
            errors += 1
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "kind": "factor", "ok": True, "trust": "sandboxed", "universe": uni.name,
        "n_scored": len(scores), "n_universe": len(uni.instruments), "errors": errors,
        "truncated": len(uni.instruments) > _MAX_FACTOR_SYMBOLS,
        "ranking": [{"symbol": k, "score": round(v, 6)} for k, v in ranked],
    }


def _run_factor_trusted(deps: Any, code: str, ctx: dict) -> dict:
    ns = _exec_trusted(code)
    cls = _find_subclass(ns, Factor)
    if cls is None:
        raise ValueError("no qhfi Factor subclass defined in the module")
    factor = cls()
    uni = get_universe(ctx.get("universe", "dow30"))
    today = date.today()
    span = DateRange(start=today - timedelta(days=int(max(1, ctx.get("years", 2)) * 365.25) + 200), end=today)
    deps.dm.update(uni, span)
    prices = deps.dm.get_panel(uni, "close", span)
    if prices.empty or prices.shape[1] < 2:
        raise ValueError(f"insufficient data for universe '{uni.name}'")
    scores = factor.signed(prices, uni)  # may raise if the factor needs multi-field panels
    last = scores.iloc[-1].dropna().sort_values(ascending=False)
    return {
        "kind": "factor", "ok": True, "trust": "trusted",
        "name": getattr(cls, "name", "") or cls.__name__, "universe": uni.name,
        "ranking": [{"symbol": str(k), "score": round(float(v), 6)} for k, v in last.items()],
    }


# ── strategy ───────────────────────────────────────────────────────────────────────
def _run_strategy_sandboxed(deps: Any, code: str, ctx: dict) -> dict:
    name = "__sandbox__"
    lab.register_custom(name, "Sandbox", ctx.get("params_defs", []), code)  # validates `code`
    try:
        out = lab.run_lab(
            deps.dm, symbol=ctx.get("symbol", "AAPL"), asset=ctx.get("asset", "equity"),
            timeframe=ctx.get("timeframe", "1d"), strategy=name, params=ctx.get("params", {}),
            direction=ctx.get("direction", "long_only"), years=int(ctx.get("years", 3)),
        )
    finally:
        lab.unregister_custom(name)
    return {"kind": "strategy", "ok": True, "trust": "sandboxed", "engine": "lab", "preview": out}


def _run_strategy_trusted(deps: Any, code: str, ctx: dict) -> dict:
    ns = _exec_trusted(code)
    cls = _find_subclass(ns, Strategy)
    if cls is None:
        raise ValueError("no qhfi Strategy subclass defined in the module")
    out = eng.run_strategy_instance(
        deps.dm, cls(), universe_name=ctx.get("universe", "dow30"),
        label=getattr(cls, "name", "") or cls.__name__, years=int(ctx.get("years", 3)),
        mode=ctx.get("mode", ""),
    )
    return {"kind": "strategy", "ok": True, "trust": "trusted", "engine": "portfolio", "preview": out}


# ── portfolio ───────────────────────────────────────────────────────────────────────
def _eval_portfolio_code(deps: Any, code: str, ctx: dict, trusted: bool) -> Any:
    if trusted:
        return _exec_trusted(code).get("result")
    tree = ac._validate(code)
    ns = {
        "__builtins__": ac._SAFE_BUILTINS, "pd": pd, "np": np, "math": math,
        "bars": lambda s, a="equity": mkt.fetch_bars(deps.dm, str(s).upper(), a)[1],
        "quote": lambda s, a="equity": mkt.quote_from_bars(mkt.fetch_bars(deps.dm, str(s).upper(), a)[1]),
        "params": dict(ctx.get("params", {})), "result": None,
    }
    exec(compile(tree, "<portfolio>", "exec"), ns)  # noqa: S102 - sandboxed + AST-allowlisted
    return ns.get("result")


def _to_allocations(raw: Any) -> list[dict]:
    """Accept result as {symbol: weight} or [{symbol, weight, asset?}] or [(symbol, weight)]."""
    rows: list[tuple[Any, Any, str]] = []
    if isinstance(raw, dict):
        rows = [(k, v, "equity") for k, v in raw.items()]
    elif isinstance(raw, (list, tuple)):
        for a in raw:
            if isinstance(a, dict):
                rows.append((a.get("symbol"), a.get("weight"), a.get("asset", "equity")))
            elif isinstance(a, (list, tuple)) and len(a) >= 2:
                rows.append((a[0], a[1], "equity"))
    else:
        raise ValueError("portfolio code must set result = {symbol: weight} or [{symbol, weight}]")
    out = []
    for sym, w, asset in rows:
        if not sym:
            continue
        try:
            out.append({"symbol": str(sym).upper(), "asset": asset or "equity", "weight": float(w)})
        except (TypeError, ValueError):
            continue
    return out


def _run_portfolio(deps: Any, code: str, ctx: dict, trusted: bool) -> dict:
    allocs = _to_allocations(_eval_portfolio_code(deps, code, ctx, trusted))
    if not allocs:
        raise ValueError("portfolio code produced no weights")
    mode = ctx.get("mode", "long_short")
    norm = pb.normalize(allocs, mode)
    alloc_value = pb.allocate(deps.dm, norm["allocations"], float(ctx.get("capital", 1_000_000)))
    return {
        "kind": "portfolio", "ok": True, "trust": "trusted" if trusted else "sandboxed", "mode": mode,
        "allocations": norm["allocations"], "exposures": norm["exposures"], "allocation": alloc_value,
    }


# ── public API ──────────────────────────────────────────────────────────────────────
def run(deps: Any, *, mode: str, trust: str, code: str, context: dict | None = None) -> dict:
    """Run a sandbox cell. Raises ValueError (→ 4xx) on bad mode/trust/code/data."""
    if mode not in MODES:
        raise ValueError(f"unknown mode '{mode}'")
    if trust not in TRUSTS:
        raise ValueError(f"unknown trust '{trust}'")
    ctx = context or {}
    trusted = trust == "trusted"
    if mode == "strategy":
        return _run_strategy_trusted(deps, code, ctx) if trusted else _run_strategy_sandboxed(deps, code, ctx)
    if mode == "factor":
        return _run_factor_trusted(deps, code, ctx) if trusted else _run_factor_sandboxed(deps, code, ctx)
    return _run_portfolio(deps, code, ctx, trusted)


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name.strip()).strip("_").lower() or "sandbox_module"


def save(
    deps: Any, *, mode: str, trust: str, name: str, code: str,
    meta: dict | None = None, allocations: list[dict] | None = None,
) -> dict:
    """Persist a sandbox cell to the home matching (mode, trust)."""
    meta = meta or {}
    store = deps.store
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")

    if mode == "portfolio":
        if not allocations:
            raise ValueError("no allocations to save — run the portfolio first")
        pb.save_portfolio(store, name, {
            "description": meta.get("description", "Built in the sandbox"),
            "mode": meta.get("mode", "long_short"), "allocations": allocations,
            "tags": ["sandbox"], "notes": meta.get("notes", ""),
        })
        return {"ok": True, "saved": "portfolio", "name": name}

    if trust == "trusted":
        key = "factors_dir" if mode == "factor" else "strategies_dir"
        target = Path(reg.get_paths(store)[key]).resolve()
        pkg = reg._qhfi_pkg_dir()
        if target == pkg or pkg in target.parents:
            raise ValueError(
                f"the linked {mode} directory still points at the qhfi package; set a custom "
                f"{key} in Settings → Linked qhfi directories before saving a trusted drop-in."
            )
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{_safe_filename(name)}.py"
        path.write_text(code, encoding="utf-8")
        loaded = reg.load_dir_modules(str(target))
        return {"ok": True, "saved": f"dropin_{mode}", "path": str(path), "loaded": loaded}

    if mode == "factor":
        reg.save_factor(store, name, {
            "kind": meta.get("kind", "alpha"), "direction": meta.get("direction", "high=long"),
            "description": meta.get("description", ""), "code": code,
        })
        return {"ok": True, "saved": "custom_factor", "name": name}
    reg.save_strategy(store, name, {
        "label": meta.get("label", name), "description": meta.get("description", ""),
        "params": meta.get("params", []), "code": code,
    })
    return {"ok": True, "saved": "custom_strategy", "name": name}


# ── starter templates (served to the widget) ────────────────────────────────────────
STARTERS: dict[str, dict[str, str]] = {
    "factor": {
        "sandboxed": (
            "# Factor formula (sandboxed). Per symbol you get close/high/low/open/volume (Series)\n"
            "# + params (dict), pd/np/math. Set `result` = a score Series (higher = more long).\n"
            "# The sandbox runs this across the universe and ranks the latest cross-section.\n"
            "mom = close / close.shift(90) - 1.0          # 90-day momentum\n"
            "vol = close.pct_change().rolling(20).std()\n"
            "result = mom / (vol + 1e-9)                   # risk-adjusted momentum\n"
        ),
        "trusted": (
            "# Factor (trusted). Define a qhfi Factor subclass; `signed()` is taken over the universe.\n"
            "from qhfi.factors.base import Factor\n"
            "from qhfi.core.types import Panel, Universe\n\n"
            "class MyFactor(Factor):\n"
            "    name = 'my_factor'\n"
            "    direction = 1\n"
            "    def compute(self, prices: Panel, universe: Universe) -> Panel:\n"
            "        return prices.pct_change(60)          # 60-day return, cross-sectional\n"
        ),
    },
    "strategy": {
        "sandboxed": (
            "# Strategy signal (sandboxed, single-symbol lab). You get close/high/low (Series) +\n"
            "# params (dict), pd/np/math. Set `result` = per-bar position in {-1, 0, 1}.\n"
            "fast = close.rolling(10).mean()\n"
            "slow = close.rolling(30).mean()\n"
            "result = np.where(fast > slow, 1, -1)\n"
        ),
        "trusted": (
            "# Strategy (trusted, portfolio engine). Define a qhfi Strategy subclass → backtested\n"
            "# over the chosen universe.\n"
            "from qhfi.strategy.base import Strategy\n"
            "from qhfi.core.types import Panel, TargetWeights, Universe\n\n"
            "class MyStrategy(Strategy):\n"
            "    name = 'my_strategy'\n"
            "    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:\n"
            "        avail = prices.notna().astype(float)  # equal-weight long-only\n"
            "        return avail.div(avail.sum(axis=1), axis=0).fillna(0.0)\n"
        ),
    },
    "portfolio": {
        "sandboxed": (
            "# Portfolio weights (sandboxed). Helpers: bars(sym) -> OHLCV, quote(sym), params, pd/np.\n"
            "# Set `result` = {symbol: weight} or [{'symbol':..,'weight':..}] (fractions or any scale;\n"
            "# the sandbox normalizes gross to 1 for the chosen mode).\n"
            "result = {'AAPL': 0.4, 'MSFT': 0.3, 'NVDA': 0.3}\n"
        ),
        "trusted": (
            "# Portfolio weights (trusted). Full Python — import qhfi, fetch data, etc.\n"
            "# Set `result` = {symbol: weight} or [{'symbol':..,'weight':..}].\n"
            "syms = ['AAPL', 'MSFT', 'NVDA', 'AMZN']\n"
            "result = {s: 1.0 / len(syms) for s in syms}\n"
        ),
    },
}


def templates() -> dict:
    return {"modes": list(MODES), "trusts": list(TRUSTS), "starters": STARTERS}
