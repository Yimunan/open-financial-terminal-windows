"""Spot FX data provider for the DataManager.

Lets the terminal treat G10 spot FX (EUR/USD, USD/JPY, …) as a first-class asset class alongside
equities, crypto and rates. yfinance quotes spot FX as ``EURUSD=X``, so this provider maps the
canonical ``BASE/QUOTE`` pair id the rest of the app uses to the yfinance symbol and delegates to
the yfinance provider. The DataManager then caches each pair under the clean id
(``market/fx/EUR_USD.parquet``) and serves quotes/bars through the normal path.

Mirrors scripts/pull_fx.py in the qhfi engine (same pair→ticker convention). Note: yfinance spot-FX
bars carry no real volume (it reports 0) — that column is meaningless for spot FX.
"""

from __future__ import annotations

from qhfi.core.types import AssetClass, Bars, DateRange, Instrument
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider


class FxProvider:
    """DataProvider for AssetClass.FX — yfinance under the hood, canonical-pair-id facing."""

    asset_class = AssetClass.FX

    def __init__(self) -> None:
        self._yf = YFinanceDataProvider()

    def _yf_symbol(self, pair_id: str) -> str:
        """'EUR/USD' -> 'EURUSD=X' (yfinance spot-FX convention); pass through an explicit =X."""
        pid = pair_id.upper()
        return pid if pid.endswith("=X") else pid.replace("/", "") + "=X"

    def fetch_daily(self, instrument: Instrument, span: DateRange) -> Bars:
        """Normalized daily OHLC for one spot-FX pair (fetched via its yfinance ``=X`` symbol)."""
        proxy = Instrument(id=self._yf_symbol(instrument.id), asset_class=AssetClass.FX)
        return self._yf.fetch_daily(proxy, span)
