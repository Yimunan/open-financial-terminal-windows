"""Algo trading — CRUD for scheduled strategies + the always-on runner controls.

Algos are persisted configs that the ``AlgoRunner`` fires on a cadence, turning a strategy's
live signal into orders on the same paper broker the Paper Trading widget uses. Two kinds:
``template`` (single-symbol StrategyLab signal) and ``xsection`` (cross-sectional factor book).
Nothing trades on save — only when armed + due (runner) or on the explicit run/preview calls.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.deps import broker_kind, get_runner, get_store

router = APIRouter(prefix="/api/algo", tags=["algo"])


class CadenceIn(BaseModel):
    kind: str = "daily"               # daily | interval
    seconds: int = 300                # interval cadence only
    at: str = "16:10"                 # daily: fire only after this local time (HH:MM)
    tz: str = "America/New_York"      # daily: timezone for `at` (default US equity close)


class RiskIn(BaseModel):
    # All optional: when omitted the runner applies kind-aware defaults (a template book may hold
    # one full-size name; an xsection book uses the diversified 0.20 per-name cap).
    max_gross: float | None = None
    max_net: float | None = None
    max_position: float | None = None
    max_drawdown_kill: float | None = None


class AlgoIn(BaseModel):
    name: str = "Untitled algo"
    kind: str = "template"       # template | xsection
    # template fields
    symbol: str = "AAPL"
    asset: str = "equity"
    timeframe: str = "1d"
    strategy: str = "sma_cross"
    params: dict = {}
    direction: str = "both"      # long_only | short_only | both
    # xsection fields
    universe: str = "dow30"
    factor: str = "momentum"
    mode: str = "long_short"     # long_short | long_only
    top_pct: float = 0.2
    # shared
    size_pct: float = 1.0        # per-name weight (template) / gross scale (xsection)
    cadence: CadenceIn = CadenceIn()
    risk: RiskIn = RiskIn()
    armed: bool = False
    book: str = "primary"        # 'primary' (Alpaca/sim) | 'sim' | 'sim:<account_id>' sandbox book


def _validate(body: AlgoIn) -> None:
    if body.kind not in ("template", "xsection"):
        raise HTTPException(400, "kind must be 'template' or 'xsection'")
    if body.cadence.kind not in ("daily", "interval"):
        raise HTTPException(400, "cadence.kind must be 'daily' or 'interval'")
    _validate_book(body.book)


def _validate_book(book: str) -> None:
    """Accept 'primary', 'sim', or 'sim:<id>' (the account must exist for the latter)."""
    if book in ("primary", "sim"):
        return
    if book.startswith("sim:"):
        from app.deps import get_store

        try:
            account_id = int(book.split(":", 1)[1])
        except (TypeError, ValueError):
            raise HTTPException(400, "book must be 'primary', 'sim', or 'sim:<account_id>'") from None
        if get_store().get_paper_account(account_id) is None:
            raise HTTPException(400, f"no such sim account: {account_id}")
        return
    raise HTTPException(400, "book must be 'primary', 'sim', or 'sim:<account_id>'")


@router.get("/strategies")
def strategies() -> dict:
    """Everything the create form needs: template signals, cross-sectional factors, universes."""
    from app.services import factors as fac
    from app.services import strategy_lab as lab
    from app.services.universe import list_universes

    return {
        "templates": lab.list_strategies(),
        "factors": [
            {"key": k, "label": v["label"], "direction": v.get("direction")}
            for k, v in fac.CATALOG.items()
        ],
        "universes": list_universes(),
        "broker": broker_kind(),
    }


@router.get("/status")
def status(runner=Depends(get_runner)) -> dict:
    return runner.status()


@router.post("/pause")
def pause(runner=Depends(get_runner)) -> dict:
    runner.set_paused(True)
    return runner.status()


@router.post("/resume")
def resume(runner=Depends(get_runner)) -> dict:
    runner.set_paused(False)
    return runner.status()


@router.get("/algos")
def list_algos(store=Depends(get_store)) -> dict:
    return {"algos": store.list_algos(), "broker": broker_kind()}


@router.post("/algos")
def create(body: AlgoIn, store=Depends(get_store)) -> dict:
    _validate(body)
    algo_id = uuid.uuid4().hex[:12]
    record = body.model_dump()
    record["last_run"] = None
    store.save_algo(algo_id, record)
    return store.get_algo(algo_id)


@router.put("/algos/{algo_id}")
def update(algo_id: str, body: AlgoIn, store=Depends(get_store)) -> dict:
    _validate(body)
    existing = store.get_algo(algo_id)
    if existing is None:
        raise HTTPException(404, f"unknown algo '{algo_id}'")
    record = body.model_dump()
    record["last_run"] = existing.get("last_run")  # preserve scheduling state across edits
    store.save_algo(algo_id, record)
    return store.get_algo(algo_id)


@router.delete("/algos/{algo_id}")
def delete(algo_id: str, store=Depends(get_store)) -> dict:
    store.remove_algo(algo_id)
    return {"ok": True}


@router.post("/algos/{algo_id}/arm")
def arm(algo_id: str, store=Depends(get_store)) -> dict:
    return _set_armed(store, algo_id, True)


@router.post("/algos/{algo_id}/disarm")
def disarm(algo_id: str, store=Depends(get_store)) -> dict:
    return _set_armed(store, algo_id, False)


def _set_armed(store, algo_id: str, value: bool) -> dict:
    algo = store.get_algo(algo_id)
    if algo is None:
        raise HTTPException(404, f"unknown algo '{algo_id}'")
    algo["armed"] = value
    store.save_algo(algo_id, algo)
    return store.get_algo(algo_id)


@router.post("/algos/{algo_id}/run")
def run_now(algo_id: str, runner=Depends(get_runner)) -> dict:
    """Run one cycle immediately (submits orders). Errors surface in the returned summary."""
    try:
        return runner.run_cycle(algo_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/preview")
def preview(body: AlgoIn, runner=Depends(get_runner)) -> dict:
    """Compute the live signal + the orders it *would* submit, without trading."""
    _validate(body)
    return runner.preview(body.model_dump())


@router.get("/algos/{algo_id}/runs")
def runs(algo_id: str, limit: int = 50, store=Depends(get_store)) -> dict:
    return {"runs": store.list_algo_runs(algo_id, min(200, max(1, limit)))}
