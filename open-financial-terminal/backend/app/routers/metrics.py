"""Metrics — per-symbol, asset-class-tailored analytics tearsheet."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from qhfi.data.manager import DataManager

from app.deps import get_data_manager
from app.services import metrics as mx

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics")
def metrics(symbol: str, asset: str = "equity", dm: DataManager = Depends(get_data_manager)) -> dict:
    return mx.metrics(dm, symbol, asset)


@router.get("/metrics/rolling")
def metrics_rolling(
    symbol: str,
    asset: str = "equity",
    window: int = 90,
    dm: DataManager = Depends(get_data_manager),
) -> dict:
    return mx.rolling(dm, symbol, asset, window)
