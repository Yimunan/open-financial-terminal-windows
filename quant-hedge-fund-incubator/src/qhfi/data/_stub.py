# NOTE: Phase-1 placeholder for the qhfi data/model layer omitted from the public
# release. Permissive stub — lets the terminal backend import and boot. Replace with
# real implementations in Phase 2 (yfinance / ccxt / FRED / EDGAR providers).

"""Permissive base: constructors accept anything; unknown methods return an empty
DataFrame (predicates -> False, list-like names -> [])."""
from __future__ import annotations

import pandas as pd


class StubBase:
    def __init__(self, *args, **kwargs):
        # root=, store=, providers=, exchange=, ... become real attributes so callers
        # that read them (e.g. dm.providers, store.root) get the passed-in value.
        self.__dict__.update(kwargs)
        self._args = args

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)

        def _method(*a, **k):
            if name.startswith(("has", "is_", "exists", "contains")):
                return False
            if name.startswith(("list", "all", "ids", "symbols", "keys", "names")):
                return []
            return pd.DataFrame()

        return _method


def empty_df(*a, **k):
    return pd.DataFrame()
