"""Offline test for the yfinance equity provider — monkeypatches yf.download to return a
yfinance-shaped frame (MultiIndex cols, tz-naive index) and checks normalization.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance

from qhfi.core.types import AssetClass, DateRange, Instrument
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider


def test_normalizes_multiindex_and_localizes_utc(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=10, freq="B")  # tz-naive business days
    # yfinance-style (Price, Ticker) MultiIndex columns, even for one ticker
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], ["AAPL"]], names=["Price", "Ticker"]
    )
    data = np.arange(len(idx) * 5, dtype=float).reshape(len(idx), 5)
    fake = pd.DataFrame(data, index=idx, columns=cols)

    monkeypatch.setattr(yfinance, "download", lambda *a, **k: fake)

    bars = YFinanceDataProvider().fetch_daily(
        Instrument(id="AAPL", asset_class=AssetClass.EQUITY),
        DateRange(start=idx[0].date(), end=idx[-1].date()),
    )
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
    assert str(bars.index.tz) == "UTC"
    assert len(bars) == 10


def test_empty_download_returns_empty_frame(monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    bars = YFinanceDataProvider().fetch_daily(
        Instrument(id="ZZZZ", asset_class=AssetClass.EQUITY),
        DateRange(start=pd.Timestamp("2024-01-01").date(), end=pd.Timestamp("2024-02-01").date()),
    )
    assert bars.empty
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
