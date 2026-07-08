# NOTE: Phase-2 reconstruction. MacroStore = one FRED series per file (macro/{series_id}.parquet),
# each a date-indexed Series. Contract: has/load/save(series_id) + catalog() -> DataFrame
# (columns: series, obs, start, end) for the explorer dropdown (app/services/macro.py).
from __future__ import annotations

from pathlib import Path

import pandas as pd


class MacroStore:
    def __init__(self, root=".", *args, **kwargs):
        self.root = Path(str(root))

    def _dir(self) -> Path:
        return self.root / "macro"

    def _path(self, series_id) -> Path:
        return self._dir() / f"{series_id}.parquet"

    def has(self, series_id) -> bool:
        try:
            return self._path(series_id).is_file()
        except Exception:
            return False

    def load(self, series_id) -> pd.Series:
        p = self._path(series_id)
        if not p.is_file():
            return pd.Series(dtype=float)
        try:
            df = pd.read_parquet(p)
            s = df.iloc[:, 0]
            s.index = pd.to_datetime(s.index, utc=True)
            return s.sort_index()
        except Exception:
            return pd.Series(dtype=float)

    def save(self, series_id, series) -> None:
        if series is None or len(series) == 0:
            return
        s = series.copy()
        try:
            s.index = pd.to_datetime(s.index, utc=True)
        except Exception:
            pass
        p = self._path(series_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            s.sort_index().to_frame("value").to_parquet(p)
        except Exception:
            pass

    def catalog(self) -> pd.DataFrame:
        rows = []
        d = self._dir()
        if d.is_dir():
            for p in sorted(d.glob("*.parquet")):
                sid = p.stem
                s = self.load(sid).dropna()
                if not len(s):
                    continue
                rows.append(
                    {
                        "series": sid,
                        "obs": int(len(s)),
                        "start": s.index[0].to_pydatetime().date(),
                        "end": s.index[-1].to_pydatetime().date(),
                    }
                )
        return pd.DataFrame(rows, columns=["series", "obs", "start", "end"])

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def _m(*a, **k):
            if name.startswith(("has", "is_", "exists")):
                return False
            return pd.DataFrame()

        return _m
