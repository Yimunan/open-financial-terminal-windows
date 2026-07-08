# NOTE: Phase-2 reconstruction. Treasury yield curve from FRED (keyless CSV). Columns match the
# terminal's TENOR_ORDER (1M..30Y); index = UTC dates. Consumed by RatesStore("treasury_curve").
from __future__ import annotations

import pandas as pd

from ..base import DataProvider
from ._fredcsv import fred_series

# tenor label -> FRED constant-maturity Treasury series id
_TENOR_FRED = {
    "1M": "DGS1MO",
    "3M": "DGS3MO",
    "6M": "DGS6MO",
    "1Y": "DGS1",
    "2Y": "DGS2",
    "3Y": "DGS3",
    "5Y": "DGS5",
    "7Y": "DGS7",
    "10Y": "DGS10",
    "20Y": "DGS20",
    "30Y": "DGS30",
}


class FredRatesProvider(DataProvider):
    def __init__(self, http=None, managed=None, *args, **kwargs):
        super().__init__(**kwargs)
        self._http = http

    def treasury_curve(self) -> pd.DataFrame:
        cols = {}
        for tenor, sid in _TENOR_FRED.items():
            s = fred_series(sid, http=self._http)
            if len(s):
                cols[tenor] = s
        if not cols:
            # FRED unreachable — raise so the caller (app/services/data_refresh) falls back to
            # its yfinance rate tickers (^IRX/^FVX/^TNX/^TYX) instead of storing an empty curve.
            raise RuntimeError("FRED treasury curve unavailable")
        curve = pd.DataFrame(cols)
        curve = curve[[t for t in _TENOR_FRED if t in curve.columns]]  # keep TENOR_ORDER
        return curve.dropna(how="all").sort_index()

    def fetch_series(self, series_id, *a, **k) -> pd.Series:
        return fred_series(series_id, http=self._http)
