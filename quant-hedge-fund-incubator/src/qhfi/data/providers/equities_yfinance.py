# NOTE: Phase-2 reconstruction. Daily equity OHLCV via yfinance, normalized to the qhfi
# Bars contract (UTC DatetimeIndex, lowercase open/high/low/close/volume). Also serves the
# terminal's FX/rates/commodity providers, which map their ids to yfinance =X / =F symbols
# and delegate to fetch_daily here.
from __future__ import annotations

from datetime import timedelta

import pandas as pd

from qhfi.core.types import AssetClass

from ..base import DataProvider, _empty_bars, normalize_index


def _norm_yf(hist: pd.DataFrame) -> pd.DataFrame:
    if hist is None or hist.empty:
        return _empty_bars()
    lower = {str(c).lower(): c for c in hist.columns}

    def col(name):
        return hist[lower[name]] if name in lower else pd.Series(index=hist.index, dtype="float64")

    frame = pd.DataFrame(
        {
            "open": col("open"),
            "high": col("high"),
            "low": col("low"),
            "close": col("close"),
            "volume": col("volume"),
        }
    )
    return normalize_index(frame)


class YFinanceDataProvider(DataProvider):
    asset_class = AssetClass.EQUITY

    def fetch_daily(self, instrument, span=None, *a, **k) -> pd.DataFrame:
        import yfinance as yf

        symbol = getattr(instrument, "id", instrument)
        kw = dict(interval="1d", auto_adjust=True)
        try:
            if span is not None:
                hist = yf.Ticker(symbol).history(
                    start=str(span.start),
                    end=str(span.end + timedelta(days=1)),  # yfinance end is exclusive
                    **kw,
                )
            else:
                hist = yf.Ticker(symbol).history(period="max", **kw)
        except Exception:
            return _empty_bars()
        return _norm_yf(hist)
