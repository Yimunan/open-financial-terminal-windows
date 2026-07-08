"""Macroeconomic indicators endpoints.

Exposes the qhfi lake's macro series (FRED + World Bank) and Treasury yield curve for the
Macro module: an indicators grid, a series explorer, the yield curve, and a cross-country panel.
All read-only — see ``app.services.macro``.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from qhfi.data.macro import MacroStore
from qhfi.data.rates import RatesStore

from app.deps import get_macro_store, get_rates_store
from app.services import macro as macro_svc

router = APIRouter(prefix="/api", tags=["macro"])


@router.get("/macro/catalog")
def catalog(store: MacroStore = Depends(get_macro_store)) -> dict:
    """Every macro series in the lake (id, label, group, coverage) for the explorer dropdown."""
    return macro_svc.catalog(store)


@router.get("/macro/grid")
def grid(store: MacroStore = Depends(get_macro_store)) -> dict:
    """Latest value + change + recent sparkline for the headline US indicators."""
    return macro_svc.grid(store)


@router.get("/macro/series/{series_id}")
def series(
    series_id: str,
    start: date | None = None,
    end: date | None = None,
    store: MacroStore = Depends(get_macro_store),
) -> dict:
    """A single macro series by FRED id (CPIAUCSL…) or World Bank id (WB_US_gdp_growth)."""
    if not store.has(series_id):
        raise HTTPException(status_code=404, detail=f"Series '{series_id}' not in the macro lake")
    return macro_svc.series(store, series_id, start, end)


@router.get("/macro/rates/curve")
def rates_curve(
    start: date | None = None,
    end: date | None = None,
    store: RatesStore = Depends(get_rates_store),
) -> dict:
    """US Treasury yield curve: current curve by tenor + a date×rates history."""
    if not store.has("treasury_curve"):
        raise HTTPException(status_code=404, detail="Treasury curve not in the rates lake")
    return macro_svc.rates_curve(store, start, end)


@router.get("/macro/cross-country")
def cross_country(
    indicator: str = Query("gdp_growth"),
    start: date | None = None,
    end: date | None = None,
    store: MacroStore = Depends(get_macro_store),
) -> dict:
    """World Bank multi-country panel for one indicator (gdp_growth / inflation / unemployment…)."""
    return macro_svc.cross_country(store, indicator, start, end)
