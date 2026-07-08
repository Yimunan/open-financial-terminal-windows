# NOTE: Phase-1 placeholder for the qhfi data/model layer omitted from the public
# release. Permissive stub — lets the terminal backend import and boot. Replace with
# real implementations in Phase 2 (yfinance / ccxt / FRED / EDGAR providers).

from __future__ import annotations

import pandas as pd


def __getattr__(name):  # PEP 562: any feature helper -> no-op returning empty frame
    def _f(*a, **k):
        return pd.DataFrame()

    return _f
