"""Factor performance monitoring: scorecard, single-factor drill-down, and saved monitor
sets with snapshot history. See `services/factor_monitor.py`.
"""

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
from app.services import factor_monitor as fm
from app.services import factor_monitor_agent as fma
from app.store import TerminalStore

router = APIRouter(prefix="/api/factor-monitor", tags=["factor_monitor"])


class ScorecardIn(BaseModel):
    universe: str = "dow30"
    factors: list[str] | None = None
    horizon: int = 5
    q: int = 5
    lookback_days: int = 504


class DetailIn(BaseModel):
    universe: str = "dow30"
    factor: str = "momentum"
    horizon: int = 5
    q: int = 5
    lookback_days: int = 504
    roll_window: int = 63


class MonitorIn(BaseModel):
    universe: str = "dow30"
    factors: list[str] = []
    horizon: int = 5
    q: int = 5
    lookback_days: int = 504
    notes: str = ""


@router.websocket("/agent")
async def factor_monitor_agent_ws(ws: WebSocket) -> None:
    """Chat-driven factor agent: `{op:"run", goal, context}` → stream thought/result/obs/done
    frames. Mirrors the backtest agent (`/api/backtest/agent`)."""
    await ws.accept()
    deps = fma.FMDeps(
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
            goal = msg.get("goal", "")
            context = msg.get("context") or {}
            try:
                async for frame in fma.arun_factor_monitor_agent(goal, context, deps):
                    await ws.send_json(frame)
            except Exception as e:  # noqa: BLE001 - never kill the socket on a run error
                await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        return


@router.post("/scorecard")
def scorecard(
    body: ScorecardIn,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
    store: TerminalStore = Depends(get_store),
) -> dict:
    try:
        return fm.scorecard(dm, fstore, fprov, body.universe, body.factors, body.horizon, body.q, body.lookback_days, store=store)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None


@router.post("/detail")
def detail(
    body: DetailIn,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
    store: TerminalStore = Depends(get_store),
) -> dict:
    try:
        return fm.factor_detail(
            dm, fstore, fprov, body.universe, body.factor, body.horizon, body.q, body.lookback_days, body.roll_window, store=store,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None


@router.post("/heatmap")
def heatmap(
    body: ScorecardIn,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
    store: TerminalStore = Depends(get_store),
) -> dict:
    try:
        return fm.correlation_matrix(dm, fstore, fprov, body.universe, body.factors, body.lookback_days, store=store)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None


@router.get("/monitors")
def list_monitors(store: TerminalStore = Depends(get_store)) -> dict:
    return fm.list_monitors(store)


@router.put("/monitors/{name}")
def save_monitor(name: str, body: MonitorIn, store: TerminalStore = Depends(get_store)) -> dict:
    fm.save_monitor(store, name, body.model_dump())
    return {"ok": True, "name": name}


@router.delete("/monitors/{name}")
def delete_monitor(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    fm.remove_monitor(store, name)
    return {"ok": True}


@router.post("/monitors/{name}/run")
def run_monitor(
    name: str,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
    store: TerminalStore = Depends(get_store),
) -> dict:
    try:
        return fm.run_monitor(dm, fstore, fprov, store, name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None


@router.get("/monitors/{name}/history")
def monitor_history(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    return fm.monitor_history(store, name)
