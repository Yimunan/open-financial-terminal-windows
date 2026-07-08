# NOTE: Phase-2 reconstruction of the qhfi data layer omitted from the public release.
# DataStore = parquet lake (market/{asset_class}/{id}.parquet); DataProvider = base with
# fetch_daily(instrument, span) -> normalized OHLCV. Bars: UTC DatetimeIndex, columns
# open/high/low/close/volume (matches qhfi.core.types.Bars and app/services/market.py).
from __future__ import annotations

from pathlib import Path

import pandas as pd

OHLCV = ["open", "high", "low", "close", "volume"]


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV)


def normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce to a sorted, tz-aware (UTC) DatetimeIndex frame."""
    if df is None or len(df) == 0:
        return _empty_bars()
    df = df.copy()
    try:
        df.index = pd.to_datetime(df.index, utc=True)
    except Exception:
        pass
    return df.sort_index()


class DataProvider:
    """Base market-data provider. Subclasses override fetch_daily(instrument, span)."""

    asset_class = None

    def __init__(self, *args, **kwargs):
        self.__dict__.update(kwargs)

    def fetch_daily(self, instrument, span=None, *a, **k) -> pd.DataFrame:
        return _empty_bars()

    def fetch_bars(self, instrument, span=None, *a, **k) -> pd.DataFrame:
        return self.fetch_daily(instrument, span)

    def fetch_bars_intraday(self, instrument, timeframe="1d", *a, **k) -> pd.DataFrame:
        return _empty_bars()

    def fetch_series(self, *a, **k) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_ohlcv(self, *a, **k) -> pd.DataFrame:
        return _empty_bars()

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def _m(*a, **k):
            return pd.DataFrame()

        return _m


class DataStore:
    """Parquet lake over one root. One file per instrument:
    ``{root}/market/{asset_class}/{id}.parquet`` (slashes in the id -> underscores)."""

    def __init__(self, root=".", *args, **kwargs):
        self.root = Path(str(root))

    def _path(self, instrument) -> Path:
        ac = getattr(getattr(instrument, "asset_class", None), "value", None) or "misc"
        iid = getattr(instrument, "id", instrument)
        safe = str(iid).replace("/", "_").replace(":", "_").replace("\\", "_")
        return self.root / "market" / str(ac) / f"{safe}.parquet"

    def has(self, instrument) -> bool:
        try:
            return self._path(instrument).is_file()
        except Exception:
            return False

    def load(self, instrument) -> pd.DataFrame:
        p = self._path(instrument)
        if not p.is_file():
            return _empty_bars()
        try:
            return normalize_index(pd.read_parquet(p))
        except Exception:
            return _empty_bars()

    def save(self, instrument, bars) -> None:
        if bars is None or len(bars) == 0:
            return
        df = normalize_index(bars)
        keep = [c for c in OHLCV if c in df.columns]
        if keep:
            df = df[keep]
        p = self._path(instrument)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.to_parquet(p)
        except Exception:
            pass

    def load_panel(self, instruments, field="close") -> pd.DataFrame:
        """Wide one-field panel (index=dates, columns=instrument ids) from the per-name files.

        Must be a real method: without it the permissive ``__getattr__`` below silently answered
        ``load_panel`` with an empty frame, which broke every panel consumer downstream (the Barra
        exposures fit died inside pandas with "no types given" on an empty quantile).
        """
        series = {}
        for inst in instruments:
            bars = self.load(inst)
            if len(bars) and field in bars.columns:
                series[getattr(inst, "id", str(inst))] = bars[field]
        if not series:
            return pd.DataFrame()
        return pd.DataFrame(series).sort_index()

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def _m(*a, **k):
            if name.startswith(("has", "is_", "exists")):
                return False
            if name.startswith(("list", "all", "ids", "symbols")):
                return []
            return pd.DataFrame()

        return _m
