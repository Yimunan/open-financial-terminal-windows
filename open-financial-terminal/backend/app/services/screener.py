"""Screener service — rank a universe by any catalog factor (price / value / quality / alpha)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from qhfi.core.types import DateRange
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.manager import DataManager
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider

from app.services import factors as fac
from app.services.universe import get_universe

# Re-exported so existing imports keep working; the catalog now lives in services.factors.
available_factors = fac.available_factors


def run_factor_screen(
    dm: DataManager,
    fstore: FundamentalsStore,
    fprov: YFinanceFundamentalsProvider,
    universe_name: str,
    factor_key: str,
    limit: int = 25,
    lookback_days: int = 400,
) -> dict:
    universe = get_universe(universe_name)
    end = date.today()
    span = DateRange(start=end - timedelta(days=lookback_days), end=end)

    dm.update(universe, span)
    panel = dm.get_panel(universe, "close", span)
    if panel.empty:
        return {"factor": factor_key, "universe": universe_name, "results": [], "coverage": 0}

    scores = fac.build_signed(dm, fstore, fprov, universe, panel, factor_key)
    valid_rows = scores.dropna(how="all")
    latest = valid_rows.iloc[-1].dropna() if len(valid_rows) else pd.Series(dtype=float)

    rets_20 = panel.pct_change(20).iloc[-1] if len(panel) > 20 else pd.Series(dtype=float)
    last_px = panel.ffill().iloc[-1]

    ranked = latest.sort_values(ascending=False)
    rows = []
    for sym, score in ranked.items():
        ins = universe.by_id(sym) if sym in universe.ids else None
        rows.append({
            "symbol": sym,
            "asset": ins.asset_class.value if ins else "equity",
            "sector": ins.sector if ins else None,
            "score": round(float(score), 5),
            "ret_20d": None if sym not in rets_20 or pd.isna(rets_20[sym]) else round(float(rets_20[sym]) * 100, 2),
            "price": None if pd.isna(last_px.get(sym, np.nan)) else round(float(last_px[sym]), 4),
        })

    return {
        "factor": factor_key,
        "universe": universe_name,
        "coverage": int(latest.shape[0]),
        "results": rows[:limit] if limit else rows,
    }
