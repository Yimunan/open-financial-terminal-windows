# NOTE: Phase-2 reconstruction. FilingsStore mainly provides a data_dir root — app/services/
# filings.py caches each ticker's filings feed as JSON under {data_dir}/{TICKER}/_feed.json.
from __future__ import annotations

from pathlib import Path

import pandas as pd


class FilingsStore:
    def __init__(self, root=".", *args, **kwargs):
        self.root = Path(str(root))
        self.data_dir = self.root  # app/services/filings.py reads filings_store.data_dir

    def has(self, *a, **k) -> bool:
        return False

    def load(self, *a, **k) -> pd.DataFrame:
        return pd.DataFrame()

    def save(self, *a, **k) -> None:
        return None

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def _m(*a, **k):
            if name.startswith(("has", "is_", "exists")):
                return False
            return pd.DataFrame()

        return _m
