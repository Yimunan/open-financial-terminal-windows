"""Event types for the event-driven backtester.

The textbook flow is ``MarketEvent → SignalEvent → OrderEvent → FillEvent``: a data handler
emits a market bar, the strategy reacts with a desired exposure, the portfolio sizes it into an
order, and the execution handler reports a fill. Each event carries a ``timestamp``; a class-level
``PRIORITY`` breaks ties *within* a timestamp so a heartbeat resolves deterministically
(market → orders → fills → signals stored → record).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import pandas as pd


@dataclass(frozen=True)
class Event:
    """Base event. ``PRIORITY`` orders same-timestamp events (lower runs first); it is a class
    attribute, not a dataclass field, so it never participates in equality/ordering of data."""

    timestamp: pd.Timestamp
    PRIORITY: ClassVar[int] = 0


@dataclass(frozen=True)
class MarketEvent(Event):
    """A new bar: the close (and optional open) of every instrument that printed at ``timestamp``.
    Instruments with no bar this timestamp are simply absent (async/mixed-frequency safe)."""

    prices: dict[str, float] = field(default_factory=dict)
    opens: dict[str, float] | None = None
    PRIORITY: ClassVar[int] = 0


@dataclass(frozen=True)
class SignalEvent(Event):
    """A strategy's desired target weights (fraction of equity per instrument) as of ``timestamp``.
    Stored by the portfolio and executed after the configured signal lag."""

    targets: dict[str, float] = field(default_factory=dict)
    PRIORITY: ClassVar[int] = 4


@dataclass(frozen=True)
class OrderEvent(Event):
    """An instruction to trade ``delta_units`` of ``instrument`` against ``ref_price``."""

    instrument: str = ""
    delta_units: float = 0.0
    ref_price: float = 0.0
    PRIORITY: ClassVar[int] = 1


@dataclass(frozen=True)
class FillEvent(Event):
    """An executed trade: ``delta_units`` filled at ``fill_price`` (vs ``ref_price``), with the
    commission and the slippage cost it incurred."""

    instrument: str = ""
    delta_units: float = 0.0
    fill_price: float = 0.0
    ref_price: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    margined: bool = False
    PRIORITY: ClassVar[int] = 2


@dataclass(frozen=True)
class RecordEvent(Event):
    """Internal end-of-heartbeat marker: apply carry, re-mark the book, and record the row for
    ``timestamp`` after all of that timestamp's fills have settled."""

    PRIORITY: ClassVar[int] = 5


# ── market-making (quoting) events ───────────────────────────────────────────────
# A quoting market-maker reacts to the *full* L2 book and posts resting two-sided limit
# quotes; these do not fit the weight→market-order chain above, so they get their own
# event types (matched by ``LimitOrderMatchingHandler``, settled via ``FillEvent``).
# Sequences are tuples so the dataclasses stay frozen/hashable like the rest.


@dataclass(frozen=True)
class BookEvent(Event):
    """A full L2 snapshot for one instrument: top-of-book + depth, plus precomputed
    ``mid``/``microprice``/``obi`` so the strategy and matcher don't recompute them."""

    instrument: str = ""
    bids: tuple[tuple[float, float], ...] = ()   # ((price, size), ...) best-first
    asks: tuple[tuple[float, float], ...] = ()
    mid: float = 0.0
    microprice: float = 0.0
    obi: float = 0.0
    PRIORITY: ClassVar[int] = 0

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else float("nan")

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else float("nan")

    @property
    def best_bid_size(self) -> float:
        return self.bids[0][1] if self.bids else 0.0

    @property
    def best_ask_size(self) -> float:
        return self.asks[0][1] if self.asks else 0.0


@dataclass(frozen=True)
class TradeEvent(Event):
    """A trade print (the tape): ``size`` traded at ``price``, ``side`` = aggressor
    ('buy'/'sell'). Lets the matcher fill resting quotes against crossing flow between books."""

    instrument: str = ""
    price: float = 0.0
    size: float = 0.0
    side: str = ""
    PRIORITY: ClassVar[int] = 0


@dataclass(frozen=True)
class QuoteEvent(Event):
    """A strategy's resting quote for one instrument. ``bid_px``/``ask_px`` of ``None``
    suppresses that side (one-sided quoting at an inventory limit)."""

    instrument: str = ""
    bid_px: float | None = None
    ask_px: float | None = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    PRIORITY: ClassVar[int] = 4
