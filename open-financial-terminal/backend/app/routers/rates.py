"""Rates endpoints — interest-rate products from the qhfi lake for the Rates module.

The Treasury yield curve (term structure) and the CME Treasury futures complex (ZT/ZF/ZN/ZB/UB/ZQ,
daily OHLCV). All read-only — see ``app.services.rates``.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from qhfi.data.base import DataStore
from qhfi.data.rates import RatesStore

from app.deps import get_rates_futures_store, get_rates_store
from app.services import rates as rates_svc

router = APIRouter(prefix="/api", tags=["rates"])


@router.get("/rates/curve")
def curve(
    start: date | None = None,
    end: date | None = None,
    store: RatesStore = Depends(get_rates_store),
) -> dict:
    """US Treasury yield curve: current curve by tenor + a date×rates history."""
    if not store.has("treasury_curve"):
        raise HTTPException(status_code=404, detail="Treasury curve not in the rates lake")
    return rates_svc.curve(store, start, end)


@router.get("/rates/futures")
def futures(store: DataStore = Depends(get_rates_futures_store)) -> dict:
    """The CME Treasury futures complex: latest quote + recent sparkline for each contract."""
    return rates_svc.futures_grid(store)


@router.get("/rates/futures/{symbol}")
def futures_bars(
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    store: DataStore = Depends(get_rates_futures_store),
) -> dict:
    """Daily OHLCV candles for one Treasury future (ZT/ZF/ZN/ZB/UB/ZQ)."""
    payload = rates_svc.futures_bars(store, symbol, start, end)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"no rates future '{symbol}' in the lake")
    return payload
