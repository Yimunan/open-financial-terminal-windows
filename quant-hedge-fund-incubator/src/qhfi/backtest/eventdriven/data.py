"""Data handlers — the source of ``MarketEvent``s for the event loop.

A handler streams bars in strict timestamp order. ``PanelDataHandler`` wraps the same wide
``(dates × instruments)`` price panel the vectorized engine consumes, so the two engines run off
identical inputs. Only instruments with a non-NaN bar at a timestamp are emitted, so a mixed
24/7-crypto + 5-day-equity book (or any irregular calendar) flows without a dense union grid.
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from qhfi.backtest.eventdriven.events import MarketEvent
from qhfi.core.types import Panel


@runtime_checkable
class DataHandler(Protocol):
    def stream(self) -> Iterator[MarketEvent]:
        """Yield ``MarketEvent``s in non-decreasing timestamp order."""
        ...


class PanelDataHandler:
    """Stream market events from a wide close panel (and an optional open panel)."""

    def __init__(self, prices: Panel, open_prices: Panel | None = None) -> None:
        self.prices = prices.sort_index()
        self.opens = open_prices

    def stream(self) -> Iterator[MarketEvent]:
        opens = self.opens
        for t, row in self.prices.iterrows():
            close = {c: float(v) for c, v in row.items() if v == v}   # drop NaN bars
            open_row = None
            if opens is not None and t in opens.index:
                open_row = {c: float(v) for c, v in opens.loc[t].items() if v == v}
            yield MarketEvent(timestamp=t, prices=close, opens=open_row)
