"""Event-driven strategy interface — the push counterpart to ``Strategy.generate_weights``.

A native ``EventStrategy`` reacts to each ``MarketEvent`` and returns the ``SignalEvent``s it
wants (target weights), reading current book state through a read-only ``BookView``. This is the
streaming shape a live loop needs.

``WeightStrategyAdapter`` bridges the existing vectorized library: it carries a precomputed
``TargetWeights`` panel and, on each bar, replays that timestamp's row as a signal — so
``FactorStrategy``/``KalmanPairsStrategy``/``ButterflyStrategy``/… run on the event engine with no
changes. (The portfolio applies the signal lag, so the panel is used as-is, unshifted.)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Protocol, runtime_checkable

from qhfi.backtest.eventdriven.events import BookEvent, MarketEvent, QuoteEvent, SignalEvent
from qhfi.core.types import TargetWeights


@runtime_checkable
class BookView(Protocol):
    """Read-only window into the portfolio, passed to a native strategy each bar."""

    def equity(self) -> float: ...
    def position(self, instrument: str) -> float: ...
    def last_price(self, instrument: str) -> float: ...


class EventStrategy(ABC):
    """Subclass and implement ``on_market`` to react to bars with target-weight signals."""

    @abstractmethod
    def on_market(self, event: MarketEvent, book: BookView) -> Iterable[SignalEvent]:
        """Return the ``SignalEvent``s (target weights) this bar produces — possibly none."""
        ...


class WeightStrategyAdapter(EventStrategy):
    """Replay a precomputed ``TargetWeights`` panel as per-bar signals."""

    def __init__(self, weights: TargetWeights) -> None:
        self._weights = weights

    def on_market(self, event: MarketEvent, book: BookView) -> list[SignalEvent]:
        if event.timestamp not in self._weights.index:
            return []
        row = self._weights.loc[event.timestamp].dropna()
        if row.empty:                          # all-NaN row → hold (matches the vectorized skip)
            return []
        targets = {str(c): float(v) for c, v in row.items()}
        return [SignalEvent(timestamp=event.timestamp, targets=targets)]


class QuotingStrategy(ABC):
    """A two-sided market-maker: react to each ``BookEvent`` and return the ``QuoteEvent``s
    to rest. The push counterpart of ``EventStrategy`` for the quoting engine — it reads
    inventory through the same read-only ``BookView`` (``book.position(instrument)``)."""

    @abstractmethod
    def on_book(self, event: BookEvent, book: BookView) -> Iterable[QuoteEvent]:
        """Return the resting ``QuoteEvent``s this book update produces — possibly none."""
        ...
