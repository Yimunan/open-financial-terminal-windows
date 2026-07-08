"""Rates futures data provider for the DataManager.

Lets the terminal treat the CME Treasury futures complex (ZT/ZF/ZN/ZB/UB/ZQ) as a first-class
asset class alongside equities and crypto. yfinance quotes these as continuous front-month ``=F``
tickers (``ZN=F`` …), so this provider maps the clean CME root id the rest of the app uses to the
yfinance symbol and delegates to the yfinance provider. The DataManager then caches them under the
clean id (``market/rates/ZN.parquet``) and serves quotes/bars through the normal path.

Mirrors scripts/pull_rates_futures.py in the qhfi engine (same id→ticker map).
"""

from __future__ import annotations

from qhfi.core.types import AssetClass, Bars, DateRange, Instrument
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider

#: CME root id → yfinance continuous front-month ticker.
YF_TICKER: dict[str, str] = {
    "ZT": "ZT=F", "ZF": "ZF=F", "ZN": "ZN=F", "ZB": "ZB=F", "UB": "UB=F", "ZQ": "ZQ=F",
}


class RatesFuturesProvider:
    """DataProvider for AssetClass.RATES — yfinance under the hood, CME-root-id facing."""

    asset_class = AssetClass.RATES

    def __init__(self) -> None:
        self._yf = YFinanceDataProvider()

    def _yf_symbol(self, instrument_id: str) -> str:
        rid = instrument_id.upper()
        return YF_TICKER.get(rid) or (rid if rid.endswith("=F") else f"{rid}=F")

    def fetch_daily(self, instrument: Instrument, span: DateRange) -> Bars:
        """Normalized daily OHLCV for one rates future (fetched via its yfinance ``=F`` ticker)."""
        proxy = Instrument(id=self._yf_symbol(instrument.id), asset_class=AssetClass.RATES)
        return self._yf.fetch_daily(proxy, span)
