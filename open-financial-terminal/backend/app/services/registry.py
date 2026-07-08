"""Registries developed separately from the live engine: custom FACTORS, custom STRATEGIES, and
a searchable repository of research MODELS (factor + strategy + universe + params bundles).

Built-in factors/strategies come from the engine (`factors.CATALOG`, `strategy_lab`); custom ones
are user-defined records persisted via the store. Custom strategies carry sandboxed Python signal
code (validated by the `agent_code` allowlist) and are registered into `strategy_lab` so they're
actually runnable in the Lab / backtest.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

from app.services import agent_code as ac
from app.services import factors as fac
from app.services import strategy_lab as lab


# ── linked directories (artifacts developed separately in the qhfi repo) ─────────
def _qhfi_pkg_dir() -> Path:
    """The installed ``qhfi`` package directory (…/src/qhfi)."""
    import qhfi

    return Path(qhfi.__file__).resolve().parent


def _qhfi_repo_dir() -> Path:
    """The qhfi repo root (…/quant-hedge-fund-incubator), two levels above the package."""
    return _qhfi_pkg_dir().parents[1]


def _models_root() -> str:
    """Default ModelRepository artifact root: honour ``QHFI_MODELS_DIR`` if absolute, else
    anchor qhfi's relative ``./models`` default to the qhfi repo root (where the CLI writes)."""
    from qhfi.core.config import get_settings

    md = get_settings().models_dir
    return str(md if md.is_absolute() else (_qhfi_repo_dir() / md).resolve())


def default_paths() -> dict:
    """Defaults: factors/strategies point at the matching qhfi *source* packages (built-ins
    live there); ``models_dir`` points at the qhfi ModelRepository *artifact* root. Users can
    override any of them to a drop-in folder of their own .py files / a model store."""
    q = _qhfi_pkg_dir()
    return {
        "factors_dir": str(q / "factors"),
        "strategies_dir": str(q / "strategy" / "library"),
        "models_dir": _models_root(),
    }


def get_paths(store: Any) -> dict:
    paths = default_paths()
    for k in list(paths):
        v = store.get_config(f"registry.{k}")
        if v:
            paths[k] = v
    return paths


def set_paths(store: Any, paths: dict) -> None:
    for k in ("factors_dir", "strategies_dir", "models_dir"):
        if paths.get(k):
            store.set_config(f"registry.{k}", str(paths[k]).strip())


def scan_dir(path: str) -> dict:
    """List the Python modules + their top-level classes/functions in a directory.

    Read-only and safe: files are AST-PARSED (never executed) to extract names + docstrings, so
    it surfaces what's developed in the linked qhfi package without importing it.
    """
    p = Path(path) if path else None
    if p is None or not p.is_dir():
        return {"path": path, "exists": False, "items": []}
    items = []
    for f in sorted(p.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip files that don't parse
            continue
        doc = (ast.get_docstring(tree) or "").strip().split("\n")[0][:200]
        symbols = [
            n.name for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and not n.name.startswith("_")
        ]
        items.append({"name": f.stem, "file": f.name, "path": str(f), "doc": doc, "symbols": symbols})
    return {"path": str(p), "exists": True, "items": items}


# ── dynamic drop-in loading ───────────────────────────────────────────────────────
# A linked factors/strategies dir can hold the user's own qhfi-style modules: importing
# them runs their @register decorators so the classes land in qhfi's factor/strategy
# registries and become listable + runnable. Unlike the AST-sandboxed *custom code* path
# (agent_code._validate), these are full Python by design — the same trust model as qhfi's
# pickle-backed model store. Files inside the installed qhfi package are skipped (already
# imported at startup; re-exec would raise duplicate-registration).

_LOADED: dict[str, float] = {}  # resolved path → mtime last imported


def load_dir_modules(path: str) -> dict:
    """Import every top-level ``*.py`` in ``path`` (skipping ``_*`` and the qhfi package
    itself), so any ``@register``-decorated Factor/Strategy registers. Cached by mtime;
    a bad file is reported, never raised. Returns ``{loaded, errors}``."""
    p = Path(path).resolve() if path else None
    if p is None or not p.is_dir():
        return {"loaded": [], "errors": []}
    pkg = _qhfi_pkg_dir()
    if p == pkg or pkg in p.parents:  # built-ins already imported by qhfi at startup
        return {"loaded": [f.stem for f in p.glob("*.py") if not f.name.startswith("_")], "errors": []}
    loaded, errors = [], []
    for f in sorted(p.glob("*.py")):
        if f.name.startswith("_"):
            continue
        key = str(f)
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if _LOADED.get(key) == mtime:
            loaded.append(f.stem)
            continue
        modname = f"qhfi_dropin_{abs(hash(key))}"
        try:
            spec = importlib.util.spec_from_file_location(modname, key)
            if spec is None or spec.loader is None:
                raise ImportError("no loader")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            spec.loader.exec_module(mod)
            _LOADED[key] = mtime
            loaded.append(f.stem)
        except Exception as e:  # noqa: BLE001 - a bad drop-in shouldn't break the listing
            errors.append({"file": f.name, "error": f"{type(e).__name__}: {e}"})
    return {"loaded": loaded, "errors": errors}


def _direction_str(value: Any) -> str:
    """Human-readable factor direction from a qhfi Factor.direction int (+1/-1)."""
    try:
        return "high=long" if int(value) >= 0 else "low=long"
    except (TypeError, ValueError):
        return "formula"


def engine_factors(store: Any) -> list[dict]:
    """Live qhfi factor registry (built-ins + anything the linked dir registered)."""
    load_dir_modules(get_paths(store)["factors_dir"])
    import qhfi.factors.alpha101  # noqa: F401 - ensure built-ins are registered
    import qhfi.factors.library  # noqa: F401
    from qhfi.factors import registry as freg

    out = []
    for name in freg.all_names():
        cls = freg.get(name)
        cat = fac.CATALOG.get(name)
        out.append({
            "name": name,
            "label": (cat or {}).get("label", name),
            "direction": cat["direction"] if cat else _direction_str(getattr(cls, "direction", 1)),
            "doc": (cls.__doc__ or "").strip().split("\n")[0][:160],
            "source": "builtin" if name in fac.CATALOG else "linked",
        })
    return out


# ── factors ──────────────────────────────────────────────────────────────────────
def list_factors(store: Any) -> dict:
    builtin = [
        {"name": k, "label": v["label"], "kind": v["kind"], "direction": v["direction"], "builtin": True}
        for k, v in fac.CATALOG.items()
    ]
    custom = [{**r, "builtin": False} for r in store.list_custom_factors()]
    return {
        "builtin": builtin,
        "custom": custom,
        "engine": engine_factors(store),
        "linked": scan_dir(get_paths(store)["factors_dir"]),
    }


def save_factor(store: Any, name: str, record: dict) -> None:
    code = (record.get("code") or "").strip()
    if code:
        ac._validate(code)  # raises ValueError on disallowed constructs
    store.save_custom_factor(
        name,
        {
            "name": name,
            "kind": record.get("kind", "alpha"),
            "direction": record.get("direction", "high=long"),
            "description": record.get("description", ""),
            "code": code,
        },
    )


def remove_factor(store: Any, name: str) -> None:
    store.remove_custom_factor(name)


# ── strategies ───────────────────────────────────────────────────────────────────
def list_strategies(store: Any) -> dict:
    from app.services import engine_strategy as eng  # local import breaks the import cycle

    builtin = [{**s, "name": s["key"], "builtin": True} for s in lab.list_strategies()]
    custom = [{**r, "builtin": False} for r in store.list_custom_strategies()]
    return {
        "builtin": builtin,
        "custom": custom,
        "engine": eng.list_engine_strategies(store),
        "linked": scan_dir(get_paths(store)["strategies_dir"]),
    }


def save_strategy(store: Any, name: str, record: dict) -> None:
    code = (record.get("code") or "").strip()
    params = record.get("params") or []
    if code:
        ac._validate(code)  # validate before persisting
    rec = {
        "name": name,
        "label": record.get("label", name),
        "description": record.get("description", ""),
        "params": params,
        "code": code,
    }
    store.save_custom_strategy(name, rec)
    if code:
        lab.register_custom(name, rec["label"], params, code)  # make it runnable now
    else:
        lab.unregister_custom(name)


def remove_strategy(store: Any, name: str) -> None:
    store.remove_custom_strategy(name)
    lab.unregister_custom(name)


def reload_custom_strategies(store: Any) -> None:
    """Register all persisted custom strategies into the lab at startup."""
    for r in store.list_custom_strategies():
        if r.get("code"):
            try:
                lab.register_custom(r["name"], r.get("label", r["name"]), r.get("params") or [], r["code"])
            except Exception:  # noqa: BLE001 - a bad saved strategy shouldn't block startup
                pass


# ── research models (searchable bundle registry) ─────────────────────────────────
def list_models(store: Any, q: str = "") -> dict:
    models = [{**r, "builtin": False} for r in store.list_models()]
    if q:
        ql = q.lower()

        def match(m: dict) -> bool:
            tags = m.get("tags") or []
            tag_str = " ".join(tags) if isinstance(tags, list) else str(tags)
            hay = " ".join(
                str(m.get(k, "")) for k in ("name", "description", "factor", "strategy", "universe", "notes")
            )
            return ql in (hay + " " + tag_str).lower()

        models = [m for m in models if match(m)]
    return {"models": models, "linked": scan_dir(get_paths(store)["models_dir"])}


# ── LLM summarization of a registry ────────────────────────────────────────────
def _factor_inventory(store: Any) -> str:
    d = list_factors(store)
    lines = [f"BUILT-IN factors ({len(d['builtin'])}):"]
    lines += [f"  - {f['name']} [{f['kind']}, {f['direction']}] {f.get('label', '')}" for f in d["builtin"]]
    lines.append(f"qhfi-ENGINE factors ({len(d['engine'])}):")
    lines += [f"  - {f['name']} ({f['source']}) {f.get('doc', '')}".rstrip() for f in d["engine"]]
    lines.append(f"CUSTOM factors ({len(d['custom'])}):")
    lines += [f"  - {f['name']} [{f.get('kind', '')}, {f.get('direction', '')}] {f.get('description', '')}".rstrip()
              for f in d["custom"]] or ["  (none)"]
    return "\n".join(lines)


def _strategy_inventory(store: Any) -> str:
    d = list_strategies(store)
    lines = [f"BUILT-IN single-symbol lab strategies ({len(d['builtin'])}):"]
    lines += [f"  - {s.get('label', s['name'])} ({s['name']}, {len(s.get('params', []))} params)" for s in d["builtin"]]
    lines.append(f"qhfi-ENGINE portfolio strategies ({len(d['engine'])}):")
    lines += [f"  - {s['name']} ({s['source']}) {s.get('doc', '')}".rstrip() for s in d["engine"]]
    lines.append(f"CUSTOM strategies ({len(d['custom'])}):")
    lines += [f"  - {s['name']} {s.get('description', '')}".rstrip() for s in d["custom"]] or ["  (none)"]
    return "\n".join(lines)


def _model_inventory(store: Any) -> str:
    models = store.list_models()
    lines = [f"MODEL bundles ({len(models)}):"]
    for m in models:
        tags = ",".join(m.get("tags") or [])
        lines.append(
            f"  - {m['name']}: factor={m.get('factor') or '-'} strategy={m.get('strategy') or '-'} "
            f"universe={m.get('universe') or '-'} mode={m.get('mode') or '-'}"
            + (f" tags=[{tags}]" if tags else "")
            + (f" — {m.get('description')}" if m.get("description") else "")
        )
    if not models:
        lines.append("  (none)")
    return "\n".join(lines)


_INVENTORY = {"factors": _factor_inventory, "strategies": _strategy_inventory, "models": _model_inventory}
_KIND_NOUN = {
    "factors": "factors (cross-sectional signals that rank instruments)",
    "strategies": "trading strategies (rules that turn signals into trades)",
    "models": "research model bundles (saved factor + strategy + universe + params recipes)",
}


def summarize_registry(llm: Any, store: Any, kind: str, model: str) -> str:
    """Ask the LLM for a tight natural-language overview of one registry's current contents."""
    if kind not in _INVENTORY:
        raise ValueError(f"unknown registry kind: {kind}")
    inventory = _INVENTORY[kind](store)
    system = (
        "You are a quant research librarian for an internal trading terminal. Given an inventory "
        f"of a user's {_KIND_NOUN[kind]}, write a concise overview in 3-5 sentences: what is "
        "present, the dominant themes or categories, and one notable gap or suggestion. Be "
        "specific (reference real names/counts from the inventory). No preamble, no bullet lists, "
        "no disclaimers. This is an internal terminal, not investment advice."
    )
    return llm.complete(system, f"Registry: {kind}\n\n{inventory}", model=model).strip()


def save_model(store: Any, name: str, record: dict) -> None:
    store.save_model(
        name,
        {
            "name": name,
            "description": record.get("description", ""),
            "factor": record.get("factor", ""),
            "strategy": record.get("strategy", ""),
            "universe": record.get("universe", ""),
            "mode": record.get("mode", ""),
            "params": record.get("params") or {},
            "tags": record.get("tags") or [],
            "notes": record.get("notes", ""),
        },
    )


def remove_model(store: Any, name: str) -> None:
    store.remove_model(name)
