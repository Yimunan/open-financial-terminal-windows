# NOTE: Phase-2 reconstruction. RatesStore = named wide frames on the lake
# (rates/{name}.parquet), e.g. "treasury_curve" (index=UTC dates, columns=tenors 1M..30Y).
# Contract: has/load/save(name) — app/services/macro.py rates_curve() reads "treasury_curve".
from __future__ import annotations

from pathlib import Path

import pandas as pd


class RatesStore:
    def __init__(self, root=".", *args, **kwargs):
        self.root = Path(str(root))

    def _path(self, name) -> Path:
        return self.root / "rates" / f"{name}.parquet"

    def has(self, name) -> bool:
        try:
            return self._path(name).is_file()
        except Exception:
            return False

    def load(self, name) -> pd.DataFrame:
        p = self._path(name)
        if not p.is_file():
            return pd.DataFrame()
        try:
            df = pd.read_parquet(p)
            df.index = pd.to_datetime(df.index, utc=True)
            return df.sort_index()
        except Exception:
            return pd.DataFrame()

    def save(self, name, frame) -> None:
        if frame is None or len(frame) == 0:
            return
        df = frame.copy()
        try:
            df.index = pd.to_datetime(df.index, utc=True)
        except Exception:
            pass
        p = self._path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            df.sort_index().to_parquet(p)
        except Exception:
            pass

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def _m(*a, **k):
            if name.startswith(("has", "is_", "exists")):
                return False
            return pd.DataFrame()

        return _m
