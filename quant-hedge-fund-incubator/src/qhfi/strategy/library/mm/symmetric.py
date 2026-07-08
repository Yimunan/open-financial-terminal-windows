"""SymmetricMM — the naive fixed-spread market-maker (the control).

Quotes a constant half-spread (in bps of mid) symmetrically around the mid, with **no
inventory skew, no volatility scaling, no imbalance signal** — only a hard inventory limit that
suppresses one side. It is the baseline a smarter quoter must beat: run it alongside
``LinearInventoryMM`` / ``AvellanedaStoikovMM`` to isolate what inventory management and the OBI
signal actually add (spread captured vs inventory variance vs adverse selection).
"""

from __future__ import annotations

from qhfi.backtest.eventdriven.events import BookEvent, QuoteEvent
from qhfi.backtest.eventdriven.strategy import BookView, QuotingStrategy
from qhfi.strategy.base import StrategyParams


class SymmetricMMParams(StrategyParams):
    half_spread_bps: float = 5.0   # constant half-spread, in bps of mid
    q_max: float = 100.0           # hard inventory limit (units) → one-sided quoting
    quote_size: float = 1.0
    join_only: bool = True         # never quote through the touch
    tick_size: float = 0.0         # round quotes to this tick; 0 → no rounding


class SymmetricMM(QuotingStrategy):
    """Fixed-spread quoter centered on the mid — no skew, no signal (the baseline)."""

    name = "SymmetricMM"
    params_model = SymmetricMMParams

    def __init__(self, params: SymmetricMMParams | None = None) -> None:
        self.params = params or SymmetricMMParams()

    def _round(self, px: float) -> float:
        t = self.params.tick_size
        return round(px / t) * t if t > 0 else px

    def on_book(self, event: BookEvent, book: BookView) -> list[QuoteEvent]:
        p = self.params
        best_bid, best_ask = event.best_bid, event.best_ask
        if not (best_bid == best_bid and best_ask == best_ask):     # NaN book → no quote
            return []

        half = event.mid * p.half_spread_bps / 1e4
        bid_px: float | None = event.mid - half
        ask_px: float | None = event.mid + half
        if p.join_only:
            bid_px = min(bid_px, best_bid)
            ask_px = max(ask_px, best_ask)
        bid_px, ask_px = self._round(bid_px), self._round(ask_px)

        q = book.position(event.instrument)
        if q >= p.q_max:
            bid_px = None
        if q <= -p.q_max:
            ask_px = None
        return [QuoteEvent(timestamp=event.timestamp, instrument=event.instrument,
                           bid_px=bid_px, ask_px=ask_px,
                           bid_size=p.quote_size, ask_size=p.quote_size)]
