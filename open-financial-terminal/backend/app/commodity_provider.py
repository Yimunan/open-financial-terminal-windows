"""Commodity futures data provider for the DataManager.

Lets the terminal treat the commodity futures complex (metals, energy, grains, softs, livestock —
GC/SI/HG/CL/BZ/NG/ZC/KC/LE/…) as a first-class asset class alongside equities, crypto, rates and FX.
yfinance quotes these as continuous front-month ``=F`` tickers (``GC=F`` …), so this provider maps
the clean CME/COMEX/NYMEX/ICE/CBOT root id the rest of the app uses to the yfinance symbol and
delegates to the yfinance provider. The DataManager then caches them under the clean id
(``market/commodity/GC.parquet``) and serves quotes/bars through the normal path.

Mirrors scripts/pull_commodities.py in the qhfi engine (same id→ticker convention).
"""

from __future__ import annotations

from qhfi.core.types import AssetClass, Bars, DateRange, Instrument
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider


class CommodityFuturesProvider:
    """DataProvider for AssetClass.COMMODITY — yfinance under the hood, clean-root-id facing."""

    asset_class = AssetClass.COMMODITY

    def __init__(self) -> None:
        self._yf = YFinanceDataProvider()

    def _yf_symbol(self, instrument_id: str) -> str:
        """'GC' -> 'GC=F' (yfinance continuous front-month); pass through an explicit =F."""
        cid = instrument_id.upper()
        return cid if cid.endswith("=F") else f"{cid}=F"

    def fetch_daily(self, instrument: Instrument, span: DateRange) -> Bars:
        """Normalized daily OHLCV for one commodity future (fetched via its yfinance ``=F`` ticker)."""
        proxy = Instrument(id=self._yf_symbol(instrument.id), asset_class=AssetClass.COMMODITY)
        return self._yf.fetch_daily(proxy, span)
