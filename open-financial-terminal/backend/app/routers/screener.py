"""Screener — rank a universe by a qhfi factor."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.manager import DataManager
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider

from app.deps import get_data_manager, get_fundamentals_provider, get_fundamentals_store
from app.services import screener as scr

router = APIRouter(prefix="/api", tags=["screener"])


class ScreenRequest(BaseModel):
    universe: str = "dow30"
    factor: str = "momentum"
    limit: int = 25


@router.get("/screen/factors")
def factors() -> dict:
    return {"factors": scr.available_factors()}


@router.post("/screen")
def screen(
    req: ScreenRequest,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
) -> dict:
    try:
        return scr.run_factor_screen(dm, fstore, fprov, req.universe, req.factor, req.limit)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None
