"""Backtest — run a factor strategy through the qhfi engine and return curve + metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.manager import DataManager
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider

from app.deps import (
    get_data_manager,
    get_fundamentals_provider,
    get_fundamentals_store,
    get_llm_client,
    get_llm_model,
    get_store,
)
from app.services import backtest as bt
from app.services import backtest_agent as bta
from app.services import backtest_proposals as btp
from app.services import engine_strategy as eng

router = APIRouter(prefix="/api", tags=["backtest"])


class BacktestRequest(BaseModel):
    universe: str = "dow30"
    factor: str = "momentum"
    mode: str = "long_short"      # long_short | long_only
    top_pct: float = 0.2
    years: int = 3
    initial_equity: float = 100_000.0
    deflate: bool = True          # also compute Deflated Sharpe vs the factor-search trials
    start: str | None = None      # defined time window (ISO date); falls back to `years` back from end
    end: str | None = None
    rebalance: str = "monthly"    # monthly | quarterly | annual
    timing: dict | None = None    # market-timing overlay: {kind:"trend",ma,floor} | {kind:"regime"} | None


@router.post("/backtest")
def backtest(
    req: BacktestRequest,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
) -> dict:
    try:
        out = bt.run_backtest(
            dm, fstore, fprov, req.universe, req.factor, req.mode,
            req.top_pct, req.years, req.initial_equity, req.deflate,
            start=req.start, end=req.end, rebalance=req.rebalance, timing=req.timing,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None
    if "error" in out:
        raise HTTPException(422, out["error"])
    return out


class ModelBacktestRequest(BaseModel):
    model: str
    years: int | None = None      # override the model's stored config (else its params / defaults)
    mode: str | None = None
    top_pct: float | None = None
    start: str | None = None
    end: str | None = None


@router.post("/backtest/model")
def backtest_model(
    req: ModelBacktestRequest,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
    store=Depends(get_store),
) -> dict:
    """Back-test a saved model bundle directly (no portfolio side effects) → dashboard payload."""
    try:
        return eng.run_model_backtest(
            dm, store, fstore, fprov, req.model,
            years=req.years, mode=req.mode, top_pct=req.top_pct, start=req.start, end=req.end,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None


@router.get("/backtest/proposals")
def backtest_proposals(
    n: int = 8,
    universe: str | None = None,
    factor: str | None = None,
    factors: str | None = None,  # comma-separated factor names to focus on
    models: str | None = None,   # comma-separated model names to focus on
    store=Depends(get_store),
) -> dict:
    """Scan saved factors + models and design runnable backtest proposals (LLM, template fallback).

    Metadata-only (no market-data fetch). Optional `universe`/`factor` seed follow-up proposals to
    the active dashboard result; `factors`/`models` scope the design to specific picks. The full
    inventory is returned so the UI can render a selector.
    """
    context = {"universe": universe, "factor": factor} if (universe or factor) else None
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] if s else None  # noqa: E731
    return btp.design_proposals(
        store, get_llm_client(), get_llm_model(),
        n=max(1, min(20, n)), context=context,
        select_factors=split(factors), select_models=split(models),
    )


@router.websocket("/backtest/agent")
async def backtest_agent_ws(ws: WebSocket) -> None:
    """`{op:"run", engine:"factor"|"lab", goal, context}` → stream thought/run/obs/done frames."""
    await ws.accept()
    deps = bta.BTDeps(
        dm=get_data_manager(),
        fstore=get_fundamentals_store(),
        fprov=get_fundamentals_provider(),
        llm=get_llm_client(),
        model=get_llm_model(),
        store=get_store(),
    )
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("op") != "run":
                await ws.send_json({"type": "error", "detail": "expected op 'run'"})
                continue
            engine = msg.get("engine", "factor")
            goal = msg.get("goal", "")
            context = msg.get("context") or {}
            try:
                async for frame in bta.arun_backtest_agent(engine, goal, context, deps):
                    await ws.send_json(frame)
            except Exception as e:  # noqa: BLE001 - never kill the socket on a run error
                await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        return
