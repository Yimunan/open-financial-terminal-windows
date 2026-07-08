"""Agent-workflow scenarios.

A *scenario* bundles two things a workflow can run under:

* **variables** — a named set of run context (universe, factor, symbol, mode, top_pct, years,
  initial, …). When a scenario is active, these OVERRIDE the matching node configs, so the same
  graph can be run under different presets without rewiring.
* **shocks** — an instantaneous market what-if: ``equity_pct`` / ``crypto_pct`` price moves and a
  ``vol_mult`` risk multiplier. Applied by the backtest node to stress its book, and usable to
  value any allocation under the shock.

Persistence is the generic registry CRUD on the store; the run context is injected by
``agent_graph`` → ``agent_nodes.run_node``.
"""

from __future__ import annotations

from typing import Any

# Keys a scenario may override in node execution (mirrors agent_nodes._SPEC_KEYS + symbol/asset).
VARIABLE_KEYS = ("universe", "symbol", "asset", "factor", "mode", "top_pct", "years", "initial")
DEFAULT_SHOCKS = {"equity_pct": 0.0, "crypto_pct": 0.0, "vol_mult": 1.0}


def default_shocks() -> dict:
    return dict(DEFAULT_SHOCKS)


def clean_shocks(raw: Any) -> dict:
    s = dict(DEFAULT_SHOCKS)
    if isinstance(raw, dict):
        for k in DEFAULT_SHOCKS:
            if raw.get(k) is not None:
                try:
                    s[k] = float(raw[k])
                except (TypeError, ValueError):
                    pass
    return s


def is_active(shocks: dict | None) -> bool:
    """A shock set that actually moves anything (non-zero price move or non-unit vol)."""
    if not shocks:
        return False
    return bool(shocks.get("equity_pct") or shocks.get("crypto_pct")) or float(shocks.get("vol_mult", 1.0)) != 1.0


def _clean_variables(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in VARIABLE_KEYS:
        if raw.get(k) not in (None, ""):
            out[k] = raw[k]
    return out


def shock_for_asset(asset: str, shocks: dict) -> float:
    """Fractional price move for an asset class under the scenario (0.10 = +10%)."""
    pct = shocks.get("crypto_pct", 0.0) if str(asset).lower() == "crypto" else shocks.get("equity_pct", 0.0)
    return float(pct) / 100.0


def stress_weights(top_weights: list[dict], shocks: dict, *, asset: str = "equity") -> dict:
    """Instantaneous P&L of a weight book under a price shock.

    ``top_weights`` is the backtest's last book (``[{symbol, weight}]`` with weight in %). For a
    one-time price move the book P&L is Σ wᵢ·shock(assetᵢ); ``vol_mult`` scales an implied risk
    band (±, as a rough 1-day move proxy) but doesn't change the central P&L.
    """
    shock = shock_for_asset(asset, shocks)
    stress_pnl_pct = sum((w.get("weight", 0.0) / 100.0) * shock for w in top_weights) * 100.0
    vol_mult = float(shocks.get("vol_mult", 1.0))
    return {
        "equity_pct": shocks.get("equity_pct", 0.0),
        "crypto_pct": shocks.get("crypto_pct", 0.0),
        "vol_mult": vol_mult,
        "stress_pnl_pct": round(stress_pnl_pct, 3),
        "n_names": len(top_weights),
    }


# ── persistence ─────────────────────────────────────────────────────────────────────
def list_scenarios(store: Any) -> dict:
    return {"scenarios": store.list_scenarios()}


def save_scenario(store: Any, name: str, record: dict) -> None:
    store.save_scenario(name, {
        "name": name,
        "description": record.get("description", ""),
        "variables": _clean_variables(record.get("variables")),
        "shocks": clean_shocks(record.get("shocks")),
    })


def remove_scenario(store: Any, name: str) -> None:
    store.remove_scenario(name)


def load_spec(store: Any, name: str) -> dict | None:
    """Resolve a saved scenario into a pipeline spec dict (variables + a ``shocks`` dict).

    Used by the ``scenario_ref`` node so a saved preset can be dropped into a graph as a source,
    not just selected as the global run context. Returns ``None`` when no such scenario exists.
    """
    rec = store.get_scenario(name) if hasattr(store, "get_scenario") else \
        next((s for s in store.list_scenarios() if s["name"] == name), None)
    if not rec:
        return None
    spec = _clean_variables(rec.get("variables"))
    spec["shocks"] = clean_shocks(rec.get("shocks"))
    return spec


def build_context(store: Any, *, scenario: str | None, context: dict | None, seed: dict | None) -> dict:
    """Resolve the run context: a named scenario (loaded from the store) merged over an inline
    context, merged over the seed. Always carries a ``shocks`` dict."""
    ctx: dict = dict(seed or {})
    ctx.update({k: v for k, v in (context or {}).items() if k != "shocks"})
    ctx["shocks"] = clean_shocks((context or {}).get("shocks"))
    if scenario:
        rec = store.get_scenario(scenario) if hasattr(store, "get_scenario") else \
            next((s for s in store.list_scenarios() if s["name"] == scenario), None)
        if rec:
            ctx.update(_clean_variables(rec.get("variables")))
            ctx["shocks"] = clean_shocks(rec.get("shocks"))
    return ctx
