# NOTE: Phase-2 reconstruction. Macro indicators from FRED (keyless CSV). MACRO_SERIES maps FRED
# series id -> label (covers the terminal's GRID_SERIES headline cards + common indicators).
from __future__ import annotations

import pandas as pd

from ..base import DataProvider
from ._fredcsv import fred_series

MACRO_SERIES: dict = {
    # headline grid (app/services/macro.py GRID_SERIES)
    "CPIAUCSL": "CPI (All Urban)",
    "GDPC1": "Real GDP",
    "UNRATE": "Unemployment Rate",
    "FEDFUNDS": "Fed Funds Rate",
    "PAYEMS": "Nonfarm Payrolls",
    "M2SL": "M2 Money Stock",
    "INDPRO": "Industrial Production",
    "UMCSENT": "Consumer Sentiment (UMich)",
    # additional common indicators
    "CPILFESL": "Core CPI",
    "PCEPI": "PCE Price Index",
    "PCEPILFE": "Core PCE",
    "GDP": "Nominal GDP",
    "DGORDER": "Durable Goods Orders",
    "HOUST": "Housing Starts",
    "RSAFS": "Retail Sales",
    "PPIACO": "PPI (All Commodities)",
    "T10YIE": "10Y Breakeven Inflation",
    "T10Y2Y": "10Y-2Y Spread",
    "VIXCLS": "VIX",
    "DTWEXBGS": "Trade-Weighted USD",
    "DCOILWTICO": "WTI Crude Oil",
    "MORTGAGE30US": "30Y Mortgage Rate",
    "ICSA": "Initial Jobless Claims",
    "CIVPART": "Labor Force Participation",
}


class MacroProvider(DataProvider):
    def __init__(self, http=None, *args, **kwargs):
        super().__init__(**kwargs)
        self._http = http

    def fetch_series(self, series_id, *a, **k) -> pd.Series:
        return fred_series(series_id, http=self._http)
