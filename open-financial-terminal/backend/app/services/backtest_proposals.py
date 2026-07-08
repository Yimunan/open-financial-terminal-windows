"""Backtest proposals: scan the built/saved factors + models and design backtests to run.

Reads the live registries (factors, research-model bundles, trained-model cards) and universes —
all metadata only, no market-data fetch — then asks the LLM to design a diverse, non-redundant set
of backtests worth running. Each proposal is runnable:

  * ``kind: "factor"`` — carries a natural-language ``prompt`` for the existing backtest agent.
  * ``kind: "model"``  — a saved *research-bundle* model run via ``POST /api/backtest/model``.

Trained ML models (the qhfi ``ModelRepository`` cards) are scanned for context only — they aren't
directly back-testable through an existing endpoint, so the LLM is told to propose ``factor`` runs
over their component factors instead, and any ``model`` proposal that isn't a known research bundle
is dropped in validation. Everything is validated/clamped against the live inventory, and a
deterministic template generator backstops a missing/garbled/slow LLM so the endpoint always
returns a useful set.
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import Any

from app.services import model_repo as mrepo
from app.services import registry as reg
from app.services.agent_assistant import _strip_to_json
from app.services.universe import list_universes

#: /proposals is a sync endpoint, so a slow/hung LLM call (the local auto-swap proxy can stall for
#: minutes when thrashing models) would block a worker thread; rapid Regenerate/Rescan or a remount
#: would then pile up and saturate the pool. Guards: a non-blocking semaphore caps it to ONE design
#: at a time (excess callers return templates instantly), and a single-worker pool + result timeout
#: bounds how long the endpoint waits — past the timeout it returns templates while the slot stays
#: held (released only when the real call finally finishes), so nothing queues or piles up.
_llm_sem = threading.BoundedSemaphore(1)
_llm_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="btp-llm")
_LLM_TIMEOUT = 35.0  # seconds; a warm design is ~10s

_MODES = ("long_only", "long_short")
_DEFAULT_UNIVERSES = ("dow30", "sp500")
#: factors we prefer for the deterministic fallback, in order (only those that actually exist are used)
_TEMPLATE_FACTORS = ("momentum", "value", "quality", "reversal", "volatility", "alpha101")


# ── inventory scan (metadata only) ────────────────────────────────────────────────
def _factor_names(store: Any) -> dict[str, str]:
    """name -> label across builtin + custom + engine factors (deduped, first label wins)."""
    fx = reg.list_factors(store)
    out: dict[str, str] = {}
    for group in ("builtin", "custom", "engine"):
        for f in fx.get(group, []) or []:
            name = f.get("name")
            if name and name not in out:
                out[name] = f.get("label") or name
    return out


def _research_models(store: Any) -> dict[str, dict]:
    """name -> bundle for saved research models (these ARE runnable via /backtest/model)."""
    return {m["name"]: m for m in reg.list_models(store).get("models", []) if m.get("name")}


def _trained_models(store: Any) -> list[dict]:
    """Trained-model cards (context only): name, framework, and component features."""
    out = []
    for m in mrepo.list_repo_models(store).get("models", []) or []:
        versions = m.get("versions") or []
        prod = m.get("production_version")
        head = next((v for v in versions if v.get("version") == prod), versions[0] if versions else {})
        out.append({
            "name": m.get("name"),
            "framework": head.get("framework"),
            "features": [f for f in (head.get("features") or []) if f],
        })
    return [m for m in out if m["name"]]


# ── small validators ──────────────────────────────────────────────────────────────
def _short(s: Any, n: int) -> str:
    return str(s or "").strip().replace("\n", " ")[:n]


def _clamp_int(v: Any, lo: int, hi: int, default: int | None) -> int | None:
    try:
        return min(hi, max(lo, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def _factor_proposal(p: dict, factors: dict[str, str], universes: list[str]) -> dict | None:
    f = p.get("factor")
    if f not in factors:
        return None
    uni = p.get("universe") if p.get("universe") in universes else universes[0]
    mode = p.get("mode") if p.get("mode") in _MODES else "long_short"
    years = _clamp_int(p.get("years"), 1, 10, 3) or 3
    prompt = _short(p.get("prompt"), 200) or f"{f} {mode.replace('_', '-')} on {uni}, {years} years"
    return {
        "kind": "factor",
        "label": _short(p.get("label"), 60) or f"{factors[f]} · {uni} · {mode.replace('_', '-')}",
        "rationale": _short(p.get("rationale"), 140),
        "prompt": prompt,
        "factor": f,
        "universe": uni,
        "mode": mode,
        "years": years,
        "source": _short(p.get("source"), 40) or f,
    }


def _model_proposal(p: dict, models: dict[str, dict]) -> dict | None:
    name = p.get("model")
    if name not in models:  # trained models / hallucinated names are dropped here
        return None
    return {
        "kind": "model",
        "label": _short(p.get("label"), 60) or f"Model · {name}",
        "rationale": _short(p.get("rationale"), 140),
        "model": name,
        "mode": p.get("mode") if p.get("mode") in _MODES else None,
        "years": _clamp_int(p.get("years"), 1, 10, None),
        "source": name,
    }


def _key(p: dict) -> tuple:
    if p["kind"] == "factor":
        return ("factor", p["factor"], p["universe"], p["mode"])
    return ("model", p["model"], p.get("mode"), p.get("years"))


def _dedup(proposals: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for p in proposals:
        k = _key(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


# ── deterministic fallback ────────────────────────────────────────────────────────
def _templates(factors: dict[str, str], models: dict[str, dict], universes: list[str], n: int) -> list[dict]:
    top = [k for k in _TEMPLATE_FACTORS if k in factors] or list(factors)[:4]
    unis = [u for u in _DEFAULT_UNIVERSES if u in universes] or universes[:1]
    out: list[dict] = []
    for f in top:
        for uni in unis:
            for mode in _MODES:
                out.append({
                    "kind": "factor",
                    "label": f"{factors[f]} · {uni} · {mode.replace('_', '-')}",
                    "rationale": f"Baseline {factors[f]} backtest on {uni}.",
                    "prompt": f"{f} {mode.replace('_', '-')} on {uni}, 3 years",
                    "factor": f, "universe": uni, "mode": mode, "years": 3,
                    "source": f, "generated": "template",
                })
    for name, b in models.items():
        out.append({
            "kind": "model",
            "label": f"Model · {name}",
            "rationale": f"Back-test the saved '{name}' bundle.",
            "model": name, "mode": None, "years": None,
            "source": name, "generated": "template",
        })
    return _dedup(out)[:n]


# ── LLM design ────────────────────────────────────────────────────────────────────
def _system() -> str:
    return (
        "You design BACKTESTS for a quant terminal. Given the available factors, saved research-model "
        "bundles, trained models, and universes, propose a DIVERSE, NON-REDUNDANT set of backtests "
        "worth running, each with a one-line rationale.\n\n"
        'Output STRICT JSON only: {"proposals": [ ... ]} with 6-10 items. Each item is either:\n'
        '  factor: {"kind":"factor","factor":<FACTOR key>,"universe":<UNIVERSE>,'
        '"mode":"long_only"|"long_short","years":<1-10>,'
        '"prompt":<NL request naming factor, mode, universe, years>,'
        '"label":<=48 chars,"rationale":<=110 chars,"source":<short tag>}\n'
        '  model: {"kind":"model","model":<RESEARCH MODEL name>,"years":<optional 1-10>,'
        '"mode":<optional long_only|long_short>,"label":...,"rationale":...,"source":<model name>}\n\n'
        "Rules:\n"
        "- Use ONLY the provided factor keys, research-model names, and universes — never invent names.\n"
        "- A factor `prompt` MUST be a runnable request, e.g. 'momentum long-short on dow30, 3 years'.\n"
        "- Trained models are NOT directly runnable: do NOT emit a `model` proposal for one. Instead "
        "propose `factor` backtests over a trained model's component factors and say so in the rationale.\n"
        "- Cover variety: a classic benchmark, a contrast (e.g. value vs quality), an under-used Alpha101 "
        "factor, a long-only vs long-short angle, and >=1 model proposal if research bundles exist.\n"
        "- No two proposals identical in (factor/model, universe, mode)."
    )


def _user(
    factors: dict[str, str],
    research: dict[str, dict],
    trained: list[dict],
    universes: list[str],
    context: dict | None,
    n: int,
    focus: dict | None = None,
) -> str:
    fac_lines = "\n".join(f"  {k} — {v}" for k, v in factors.items())
    if research:
        rm_lines = "\n".join(
            f"  {m['name']} — {(m.get('factor') or m.get('strategy') or '?')} / "
            f"{m.get('universe') or '?'} / {m.get('mode') or '?'}"
            for m in research.values()
        )
    else:
        rm_lines = "  (none saved)"
    if trained:
        tm_lines = "\n".join(
            f"  {m['name']} — {m.get('framework') or '?'} — features: {', '.join(m['features'][:8]) or '?'}"
            for m in trained
        )
    else:
        tm_lines = "  (none)"
    ctx = ""
    if context:
        uni = context.get("universe")
        fct = context.get("factor")
        if uni or fct:
            ctx = f"\nCONTEXT (active result): universe={uni or '—'}, factor={fct or '—'}\n"
    foc = ""
    if focus and (focus.get("factors") or focus.get("models")):
        parts = []
        if focus.get("factors"):
            parts.append("factors: " + ", ".join(focus["factors"]))
        if focus.get("models"):
            parts.append("models: " + ", ".join(focus["models"]))
        foc = (
            "\nFOCUS: the user selected specific items — propose ONLY backtests that exercise these "
            + "; ".join(parts)
            + ". For a selected trained model, propose factor backtests over its component features.\n"
        )
    return (
        f"FACTORS (key — label):\n{fac_lines}\n\n"
        f"RESEARCH MODELS (name — factor|strategy / universe / mode):\n{rm_lines}\n\n"
        f"TRAINED MODELS (name — framework — features):\n{tm_lines}\n\n"
        f"UNIVERSES: {', '.join(universes)}\n{ctx}{foc}\n"
        f"Design about {n} proposals now. Return STRICT JSON only."
    )


def design_proposals(
    store: Any,
    llm: Any,
    model: str,
    *,
    n: int = 8,
    context: dict | None = None,
    select_factors: list[str] | None = None,
    select_models: list[str] | None = None,
) -> dict:
    """Scan the inventory and design ~n runnable backtest proposals (LLM, template fallback).

    `select_factors` / `select_models` scope the design to specific items the user picked (an empty
    selection means "anything"); a selected trained model contributes its component factors so we can
    propose runnable factor backtests over them. The full inventory is returned so the UI can render
    the selector.
    """
    all_factors = _factor_names(store)
    research = _research_models(store)
    trained = _trained_models(store)
    universes = list_universes() or ["dow30"]

    sel_f = {f for f in (select_factors or []) if f}
    sel_m = {m for m in (select_models or []) if m}

    if sel_f or sel_m:
        trained_by_name = {m["name"]: m for m in trained}
        feat = {f for name in sel_m for f in (trained_by_name.get(name, {}).get("features") or [])}
        factors = {k: v for k, v in all_factors.items() if k in (sel_f | feat)}
        research_pool = {k: v for k, v in research.items() if k in sel_m}
        trained_pool = [m for m in trained if m["name"] in sel_m]
        focus = {"factors": sorted(sel_f), "models": sorted(sel_m)}
    else:
        factors, research_pool, trained_pool, focus = all_factors, research, trained, None

    proposals: list[dict] = []
    # Design via the LLM only if a slot is free (else another design is in flight → use templates).
    use_llm = bool(llm is not None and (factors or research_pool)) and _llm_sem.acquire(blocking=False)
    if use_llm:
        def _job() -> Any:
            try:
                return llm.complete(
                    _system(),
                    _user(factors, research_pool, trained_pool, universes, context, n, focus),
                    model=model, temperature=0.4,
                )
            finally:
                _llm_sem.release()  # held until the call truly ends, even if we timed out below

        try:
            raw = _llm_pool.submit(_job).result(timeout=_LLM_TIMEOUT)
            data = _strip_to_json(raw)
            items = data.get("proposals") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for p in items or []:
                if not isinstance(p, dict):
                    continue
                v = _model_proposal(p, research_pool) if p.get("kind") == "model" else _factor_proposal(p, factors, universes)
                if v:
                    v["generated"] = "llm"
                    proposals.append(v)
        except Exception:  # noqa: BLE001 - timeout / LLM down / garbled → fall back to templates below
            proposals = []

    proposals = _dedup(proposals)

    # Guarantee a runnable model card for every focused research model (the user explicitly picked it
    # to test it, so surface it even if the LLM proposed a factor run instead). Prepend so the cap
    # below can't drop them.
    have_models = {p["model"] for p in proposals if p["kind"] == "model"}
    forced = [
        {
            "kind": "model", "label": f"Model · {name}",
            "rationale": f"Back-test the saved '{name}' bundle as-is.",
            "model": name, "mode": None, "years": None, "source": name, "generated": "template",
        }
        for name in research_pool
        if (sel_m & {name}) and name not in have_models
    ]
    proposals = _dedup(forced + proposals)

    # Backfill from deterministic templates if the LLM gave too few valid items (or none).
    if len(proposals) < max(4, n // 2):
        for t in _templates(factors, research_pool, universes, n):
            if _key(t) not in {_key(p) for p in proposals}:
                proposals.append(t)

    proposals = proposals[:n]
    for i, p in enumerate(proposals, 1):
        p["id"] = f"p{i}"
        p.setdefault("generated", "template")

    inventory = {
        "factors": [{"name": k, "label": v} for k, v in all_factors.items()],
        "models": (
            [{"name": k, "kind": "research"} for k in research]
            + [{"name": m["name"], "kind": "trained"} for m in trained]
        ),
    }
    return {
        "proposals": proposals,
        "inventory": inventory,
        "counts": {
            "factors": len(all_factors),
            "research_models": len(research),
            "trained_models": len(trained),
            "universes": len(universes),
        },
    }
