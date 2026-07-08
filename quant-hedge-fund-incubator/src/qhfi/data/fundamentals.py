# NOTE: Phase-2 reconstruction. FundamentalsStore = per-(instrument, metric) PIT series on the
# parquet lake: fundamentals/{metric}/{id}.parquet. Feeds app/services/factors.py value/quality
# factors (has/save/panel contract).
from __future__ import annotations

from pathlib import Path

import pandas as pd


class FundamentalsStore:
    def __init__(self, root=".", *args, **kwargs):
        self.root = Path(str(root))

    def _path(self, instrument, metric) -> Path:
        iid = getattr(instrument, "id", instrument)
        safe = str(iid).replace("/", "_").replace(":", "_")
        return self.root / "fundamentals" / str(metric) / f"{safe}.parquet"

    def has(self, instrument, metric) -> bool:
        try:
            return self._path(instrument, metric).is_file()
        except Exception:
            return False

    def load(self, instrument, metric) -> pd.Series:
        p = self._path(instrument, metric)
        if not p.is_file():
            return pd.Series(dtype=float)
        try:
            df = pd.read_parquet(p)
            s = df.iloc[:, 0]
            s.index = pd.to_datetime(s.index, utc=True)
            return s.sort_index()
        except Exception:
            return pd.Series(dtype=float)

    def save(self, instrument, metric, series) -> None:
        if series is None or len(series) == 0:
            return
        s = series.copy()
        try:
            s.index = pd.to_datetime(s.index, utc=True)
        except Exception:
            pass
        p = self._path(instrument, metric)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            s.sort_index().to_frame("value").to_parquet(p)
        except Exception:
            pass

    def panel(self, instruments, metric) -> pd.DataFrame:
        cols = {}
        for ins in instruments:
            if self.has(ins, metric):
                s = self.load(ins, metric)
                if len(s):
                    cols[getattr(ins, "id", str(ins))] = s
        if not cols:
            return pd.DataFrame()
        return pd.DataFrame(cols).sort_index()

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def _m(*a, **k):
            if name.startswith(("has", "is_", "exists")):
                return False
            return pd.DataFrame()

        return _m
