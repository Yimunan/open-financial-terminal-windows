"""Strategy Lab — single-instrument signal backtests, parameter sweeps."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from qhfi.data.manager import DataManager

from app.deps import get_data_manager
from app.services import market as mkt
from app.services import strategy_lab as lab

router = APIRouter(prefix="/api/lab", tags=["lab"])


@router.get("/strategies")
def strategies() -> dict:
    return {"strategies": lab.list_strategies()}


class LabRequest(BaseModel):
    symbol: str = "AAPL"
    asset: str = "equity"
    timeframe: str = "1d"
    strategy: str = "sma_cross"
    params: dict = {}
    direction: str = "long_only"  # long_only | short_only | both
    sl_pct: float = 0.0           # 0 disables
    tp_pct: float = 0.0
    initial: float = 100_000.0
    commission_bps: float = 5.0
    size_pct: float = 1.0
    leverage: float = 1.0
    years: int = 3


def _load_bars(req: LabRequest, dm: DataManager):
    if lab._template(req.strategy) is None:
        raise HTTPException(400, f"unknown strategy '{req.strategy}'")
    intraday = req.timeframe != "1d"
    if intraday:
        try:
            _, frame = mkt.fetch_bars_intraday(req.symbol, req.asset, req.timeframe)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    else:
        end = date.today()
        start = end.replace(year=end.year - max(1, req.years))
        _, frame = mkt.fetch_bars(dm, req.symbol, req.asset, start, end)
    if frame.empty or len(frame) < 30:
        raise HTTPException(404, f"insufficient data for {req.symbol} ({req.timeframe})")
    return frame, intraday


@router.post("/run")
def run(req: LabRequest, dm: DataManager = Depends(get_data_manager)) -> dict:
    try:
        return lab.run_lab(
            dm, symbol=req.symbol, asset=req.asset, timeframe=req.timeframe,
            strategy=req.strategy, params=req.params, direction=req.direction,
            sl_pct=req.sl_pct, tp_pct=req.tp_pct, initial=req.initial,
            commission_bps=req.commission_bps, size_pct=req.size_pct,
            leverage=req.leverage, years=req.years,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


class SweepRequest(LabRequest):
    x_key: str = "fast"
    x_from: float = 10
    x_to: float = 50
    x_step: float = 5
    y_key: str | None = None
    y_from: float = 0
    y_to: float = 0
    y_step: float = 1
    metric: str = "net_pnl_pct"


def _arange(a: float, b: float, step: float) -> list[float]:
    if step <= 0:
        return [a]
    out, v = [], a
    while v <= b + 1e-9 and len(out) < 40:
        out.append(round(v, 4))
        v += step
    return out


@router.post("/sweep")
def sweep(req: SweepRequest, dm: DataManager = Depends(get_data_manager)) -> dict:
    frame, intraday = _load_bars(req, dm)
    x_vals = _arange(req.x_from, req.x_to, req.x_step)
    y_vals = _arange(req.y_from, req.y_to, req.y_step) if req.y_key else []
    return lab.sweep(
        frame, req.strategy, req.params, req.x_key, x_vals, req.y_key, y_vals, req.metric,
        direction=req.direction, sl_pct=req.sl_pct, tp_pct=req.tp_pct,
        initial=req.initial, commission_bps=req.commission_bps,
        size_pct=req.size_pct, leverage=req.leverage, intraday=intraday,
    )
