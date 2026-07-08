# NOTE: Phase-1 placeholder for the qhfi data/model layer omitted from the public
# release. Permissive stub — lets the terminal backend import and boot. Replace with
# real implementations in Phase 2 (yfinance / ccxt / FRED / EDGAR providers).
from __future__ import annotations

from ._stub import StubBase


class CusipTickerStore(StubBase):
    """Placeholder crosswalk store."""
