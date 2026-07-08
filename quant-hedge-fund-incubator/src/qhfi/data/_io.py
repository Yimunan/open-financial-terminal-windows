# NOTE: Phase-1 placeholder for the qhfi data/model layer omitted from the public
# release. Permissive stub — lets the terminal backend import and boot. Replace with
# real implementations in Phase 2 (yfinance / ccxt / FRED / EDGAR providers).

from __future__ import annotations

import pandas as pd


def read_columns(path, columns=None, *a, **k):
    return pd.DataFrame(columns=list(columns) if columns else None)


def read_parquet(*a, **k):
    return pd.DataFrame()


def write_parquet(*a, **k):
    return None


def read_table(*a, **k):
    return pd.DataFrame()
