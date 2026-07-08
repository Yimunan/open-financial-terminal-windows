"""Registry endpoints: custom factors, custom strategies, and research models.

Built-in factors/strategies are returned read-only alongside the user's custom records; custom
records are full CRUD. Models are a searchable bundle registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from qhfi.data.manager import DataManager

from app.deps import get_data_manager, get_llm_client, get_llm_model, get_store
from app.services import engine_strategy as eng
from app.services import model_repo as mrepo
from app.services import portfolio_book as pb
from app.services import registry as reg
from app.store import TerminalStore

router = APIRouter(prefix="/api/registry", tags=["registry"])


class PathsIn(BaseModel):
    factors_dir: str | None = None
    strategies_dir: str | None = None
    models_dir: str | None = None


@router.get("/paths")
def paths(store: TerminalStore = Depends(get_store)) -> dict:
    return reg.get_paths(store)


@router.put("/paths")
def save_paths(body: PathsIn, store: TerminalStore = Depends(get_store)) -> dict:
    reg.set_paths(store, body.model_dump(exclude_none=True))
    return reg.get_paths(store)


class FactorIn(BaseModel):
    kind: str = "alpha"
    direction: str = "high=long"
    description: str = ""
    code: str = ""


class StrategyIn(BaseModel):
    label: str = ""
    description: str = ""
    params: list[dict] = []
    code: str = ""


class ModelIn(BaseModel):
    description: str = ""
    factor: str = ""
    strategy: str = ""
    universe: str = ""
    mode: str = ""
    params: dict = {}
    tags: list[str] = []
    notes: str = ""


# ── factors ──────────────────────────────────────────────────────────────────────
@router.get("/factors")
def factors(store: TerminalStore = Depends(get_store)) -> dict:
    return reg.list_factors(store)


@router.put("/factors/{name}")
def save_factor(name: str, body: FactorIn, store: TerminalStore = Depends(get_store)) -> dict:
    try:
        reg.save_factor(store, name, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "name": name}


@router.delete("/factors/{name}")
def delete_factor(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    reg.remove_factor(store, name)
    return {"ok": True}


# ── strategies ───────────────────────────────────────────────────────────────────
@router.get("/strategies")
def strategies(store: TerminalStore = Depends(get_store)) -> dict:
    return reg.list_strategies(store)


@router.put("/strategies/{name}")
def save_strategy(name: str, body: StrategyIn, store: TerminalStore = Depends(get_store)) -> dict:
    try:
        reg.save_strategy(store, name, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "name": name}


@router.delete("/strategies/{name}")
def delete_strategy(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    reg.remove_strategy(store, name)
    return {"ok": True}


class EngineRunIn(BaseModel):
    strategy_key: str
    universe_name: str = "dow30"
    years: int = 3
    initial_equity: float = 100_000.0
    mode: str = ""
    params: dict = {}
    start: str | None = None
    end: str | None = None


@router.post("/strategies/run")
def run_engine_strategy(
    body: EngineRunIn,
    dm: DataManager = Depends(get_data_manager),
    store: TerminalStore = Depends(get_store),
) -> dict:
    """Run a qhfi strategy (built-in or drop-in) through the engine over a universe."""
    try:
        return eng.run_engine_strategy(
            dm, store,
            strategy_key=body.strategy_key, universe_name=body.universe_name,
            years=body.years, initial_equity=body.initial_equity, mode=body.mode,
            params=body.params, start=body.start, end=body.end,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None


# ── models ───────────────────────────────────────────────────────────────────────
@router.get("/models")
def models(q: str = "", store: TerminalStore = Depends(get_store)) -> dict:
    return reg.list_models(store, q)


@router.put("/models/{name}")
def save_model(name: str, body: ModelIn, store: TerminalStore = Depends(get_store)) -> dict:
    reg.save_model(store, name, body.model_dump())
    return {"ok": True, "name": name}


@router.delete("/models/{name}")
def delete_model(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    reg.remove_model(store, name)
    return {"ok": True}


class SummarizeIn(BaseModel):
    kind: str  # factors | strategies | models


@router.post("/summarize")
def summarize(body: SummarizeIn, store: TerminalStore = Depends(get_store)) -> dict:
    """LLM overview of one registry's current contents (read-only; nothing persisted)."""
    try:
        text = reg.summarize_registry(get_llm_client(), store, body.kind, get_llm_model())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:  # noqa: BLE001 - surface LLM/proxy issues as 502
        raise HTTPException(502, f"LLM error: {type(e).__name__}") from None
    return {"kind": body.kind, "summary": text}


# ── trained-model repository (linked qhfi ModelRepository) ─────────────────────────
class PromoteIn(BaseModel):
    version: int
    stage: str  # dev | staging | production | archived


@router.get("/repo-models")
def repo_models(store: TerminalStore = Depends(get_store)) -> dict:
    return mrepo.list_repo_models(store)


@router.post("/repo-models/{name}/promote")
def promote_repo_model(name: str, body: PromoteIn, store: TerminalStore = Depends(get_store)) -> dict:
    try:
        return mrepo.promote_model(store, name, body.version, body.stage)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None


# ── portfolios (saved weight + allocation lists) ───────────────────────────────────
class AllocationIn(BaseModel):
    symbol: str
    asset: str = "equity"
    weight: float = 0.0


class PortfolioIn(BaseModel):
    description: str = ""
    mode: str = "long_short"  # long_only | long_short
    allocations: list[AllocationIn] = []
    tags: list[str] = []
    notes: str = ""


class NormalizeIn(BaseModel):
    allocations: list[AllocationIn] = []
    mode: str = "long_short"


class AllocateIn(BaseModel):
    allocations: list[AllocationIn] = []
    capital: float = 100_000.0


@router.get("/portfolios")
def portfolios(q: str = "", store: TerminalStore = Depends(get_store)) -> dict:
    return pb.list_portfolios(store, q)


@router.put("/portfolios/{name}")
def save_portfolio(name: str, body: PortfolioIn, store: TerminalStore = Depends(get_store)) -> dict:
    pb.save_portfolio(store, name, body.model_dump())
    return {"ok": True, "name": name}


@router.delete("/portfolios/{name}")
def delete_portfolio(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    pb.remove_portfolio(store, name)
    return {"ok": True}


@router.post("/portfolios/normalize")
def normalize_portfolio(body: NormalizeIn) -> dict:
    """Scale a weight list to gross 1 (dollar-neutral for long_short) — preview, no persistence."""
    return pb.normalize([a.model_dump() for a in body.allocations], body.mode)


@router.post("/portfolios/allocate")
def allocate_portfolio(body: AllocateIn, dm: DataManager = Depends(get_data_manager)) -> dict:
    """Value a weight list into notionals + shares at current prices."""
    return pb.allocate(dm, [a.model_dump() for a in body.allocations], body.capital)
