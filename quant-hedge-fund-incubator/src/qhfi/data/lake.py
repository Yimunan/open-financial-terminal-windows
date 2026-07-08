# NOTE: Phase-1 placeholder for the qhfi data/model layer omitted from the public
# release. Permissive stub — lets the terminal backend import and boot. Replace with
# real implementations in Phase 2 (yfinance / ccxt / FRED / EDGAR providers).

from __future__ import annotations

from pathlib import Path

from .base import DataStore


def lake_root(*a, **k):
    if a and a[0]:
        return Path(str(a[0]))
    return Path(str(k.get("root", ".")))


def market_store(*a, **k):
    return DataStore(root=lake_root(*a, **k))
