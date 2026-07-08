"""LinearInventoryMM — a practical, scale-free inventory-aware market-maker.

A deployable alternative to the textbook Avellaneda–Stoikov quoter, parametrized entirely in
**bps of mid** so it behaves the same on a $1 token and a $60k coin (no per-asset spread/skew
calibration). Three levers, each intuitive:

  * **half-spread** — a base width (bps), optionally widened by recent volatility.
  * **inventory skew** — shift the quote *center* by ``(q/q_max)·skew_bps`` so a long book quotes
    lower (leans to sell) and a short book quotes higher — the same flatten-toward-zero pressure
    Avellaneda–Stoikov gets from ``−q·γσ²τ``, but expressed directly in bps and bounded by ``q_max``.
  * **OBI tilt** — nudge the center toward the order-book-imbalance / microprice signal.

Where ``AvellanedaStoikovMM``'s spread is dimensionful (absolute price units → γ, κ must be tuned
to the asset), this one's are scale-free, so the defaults are usable out of the box.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from qhfi.backtest.eventdriven.events import BookEvent, QuoteEvent
from qhfi.backtest.eventdriven.strategy import BookView, QuotingStrategy
from qhfi.strategy.base import StrategyParams


class LinearInventoryMMParams(StrategyParams):
    half_spread_bps: float = 5.0   # base half-spread (bps of mid)
    skew_bps: float = 8.0          # center shift at full inventory ±q_max (bps of mid)
    obi_alpha: float = 0.5         # OBI tilt on the center (fraction of the half-spread)
    vol_gain: float = 0.0          # widen the half-spread by vol_gain × realized-vol (bps); 0 → off
    sigma_window: int = 100        # snapshots of mid history for the vol estimate
    q_max: float = 100.0           # hard inventory limit (units) → one-sided quoting
    quote_size: float = 1.0
    join_only: bool = True         # never quote through the touch
    tick_size: float = 0.0         # round quotes to this tick; 0 → no rounding


class LinearInventoryMM(QuotingStrategy):
    """Bps-parametrized quoter: base spread + linear inventory skew + OBI center tilt."""

    name = "LinearInventoryMM"
    params_model = LinearInventoryMMParams

    def __init__(self, params: LinearInventoryMMParams | None = None) -> None:
        self.params = params or LinearInventoryMMParams()
        self._mid_hist: dict[str, deque[float]] = {}

    def _sigma_bps(self, sym: str, mid: float) -> float:
        """Per-snapshot realized vol of mid log-returns, in bps."""
        hist = self._mid_hist.setdefault(sym, deque(maxlen=self.params.sigma_window + 1))
        hist.append(mid)
        if len(hist) < 2:
            return 0.0
        return float(np.std(np.diff(np.log(np.asarray(hist, dtype=float))), ddof=0)) * 1e4

    def _round(self, px: float) -> float:
        t = self.params.tick_size
        return round(px / t) * t if t > 0 else px

    def on_book(self, event: BookEvent, book: BookView) -> list[QuoteEvent]:
        p = self.params
        sym = event.instrument
        best_bid, best_ask = event.best_bid, event.best_ask
        if not (best_bid == best_bid and best_ask == best_ask):     # NaN book → no quote
            return []

        sigma_bps = self._sigma_bps(sym, event.mid)
        q = book.position(sym)

        # Half-spread in price units, optionally vol-widened.
        half = event.mid * (p.half_spread_bps + p.vol_gain * sigma_bps) / 1e4

        # Center: microprice nudged by OBI, then skewed by inventory to flatten toward zero.
        center = event.microprice + p.obi_alpha * event.obi * half
        if p.q_max > 0:
            lean = float(np.clip(q / p.q_max, -1.0, 1.0))           # +long / −short
            center -= lean * event.mid * p.skew_bps / 1e4           # long → lower quotes (sell bias)

        bid_px: float | None = center - half
        ask_px: float | None = center + half
        if p.join_only:
            bid_px = min(bid_px, best_bid)
            ask_px = max(ask_px, best_ask)
        bid_px, ask_px = self._round(bid_px), self._round(ask_px)

        if q >= p.q_max:
            bid_px = None
        if q <= -p.q_max:
            ask_px = None
        return [QuoteEvent(timestamp=event.timestamp, instrument=event.instrument,
                           bid_px=bid_px, ask_px=ask_px,
                           bid_size=p.quote_size, ask_size=p.quote_size)]
