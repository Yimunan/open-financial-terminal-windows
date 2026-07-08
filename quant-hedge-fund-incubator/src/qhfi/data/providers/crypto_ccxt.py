# NOTE: Phase-2 reconstruction. Daily crypto OHLCV via ccxt (default exchange passed by the
# terminal, e.g. kraken), paginated and normalized to the qhfi Bars contract.
from __future__ import annotations

import pandas as pd

from qhfi.core.types import AssetClass

from ..base import DataProvider, _empty_bars

_DAY_MS = 86_400_000


class CcxtDataProvider(DataProvider):
    asset_class = AssetClass.CRYPTO

    def __init__(self, exchange="binance", *args, **kwargs):
        super().__init__(**kwargs)
        self.exchange = exchange or "binance"

    def fetch_daily(self, instrument, span=None, *a, **k) -> pd.DataFrame:
        import ccxt

        symbol = getattr(instrument, "id", instrument)
        try:
            ex = getattr(ccxt, self.exchange)({"enableRateLimit": True})
        except Exception:
            return _empty_bars()

        since = end_ms = None
        if span is not None:
            since = int(pd.Timestamp(str(span.start), tz="UTC").timestamp() * 1000)
            end_ms = int(pd.Timestamp(str(span.end), tz="UTC").timestamp() * 1000)

        rows, cursor, limit = [], since, 720
        try:
            while True:
                batch = ex.fetch_ohlcv(symbol, timeframe="1d", since=cursor, limit=limit)
                if not batch:
                    break
                rows += batch
                if len(batch) < limit:
                    break
                cursor = batch[-1][0] + _DAY_MS
                if end_ms and cursor > end_ms:
                    break
        except Exception:
            if not rows:
                return _empty_bars()

        if not rows:
            return _empty_bars()
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates("ts")
        df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
        return df.sort_index()
