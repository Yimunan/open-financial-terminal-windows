"""Avellaneda–Stoikov market-maker with an order-book-imbalance fair value.

Separation of concerns: the **OBI/microprice** signal sets the *center* of the quotes
(an alpha-aware fair value ``s``); the **Avellaneda–Stoikov** model sets the *inventory skew*
and the *spread width*. Per book update:

    s     = clamp( microprice + α·OBI·halfspread , best_bid, best_ask )   # fair value (center)
    r     = s − q·γ·σ²·τ                                                   # reservation price (skew)
    δ     = γ·σ²·τ + (2/γ)·ln(1 + γ/κ)                                     # optimal spread (width)
    bid   = r − δ/2 ,  ask = r + δ/2                                       # quotes

with ``q`` the signed inventory (read from the book), ``σ`` rolling realized vol of the mid,
``κ`` the order-arrival decay, ``γ`` risk aversion, and ``τ`` a constant horizon (the
infinite-horizon simplification — a continuously-running crypto MM has no terminal time).

Inventory control: hard limits ``±q_max`` suppress the inventory-increasing side (one-sided
quoting); with ``inv_soften`` the offending side's half-spread widens with ``|q|`` *before*
suppression, so the transition is continuous. See ``qhfi.data.microstructure`` for the signal
math and Avellaneda & Stoikov (2008); Stoikov (2018); Cartea–Jaimungal–Penalva (2015).
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from qhfi.backtest.eventdriven.events import BookEvent, QuoteEvent
from qhfi.backtest.eventdriven.strategy import BookView, QuotingStrategy
from qhfi.strategy.base import StrategyParams


class ASParams(StrategyParams):
    gamma: float = 0.1            # risk aversion: higher → wider spread + stronger inventory skew
    sigma_window: int = 100       # snapshots of mid history for realized vol
    kappa: float = 1.5            # order-arrival decay λ(δ)=A·e^{−κδ}; lower → wider spread
    horizon: float = 1.0          # τ: constant remaining horizon (in snapshots)
    obi_alpha: float = 0.5        # OBI tilt on fair value (fraction of half-spread); 0 → pure microprice
    q_max: float = 100.0          # hard inventory limit (units) → one-sided quoting
    quote_size: float = 1.0       # size posted per side
    join_only: bool = True        # never quote through the touch (rest at/behind best)
    inv_soften: bool = True       # widen the inventory-increasing side as |q|→q_max
    tick_size: float = 0.0        # round quotes to this tick; 0 → no rounding


class AvellanedaStoikovMM(QuotingStrategy):
    """OBI-centered Avellaneda–Stoikov quoting market-maker."""

    name = "AvellanedaStoikovMM"
    params_model = ASParams

    def __init__(self, params: ASParams | None = None) -> None:
        self.params = params or ASParams()
        self._mid_hist: dict[str, deque[float]] = {}

    def _sigma(self, sym: str, mid: float) -> float:
        hist = self._mid_hist.setdefault(sym, deque(maxlen=self.params.sigma_window + 1))
        hist.append(mid)
        if len(hist) < 2:
            return 0.0
        rets = np.diff(np.log(np.asarray(hist, dtype=float)))
        return float(np.std(rets, ddof=0))

    def _round(self, px: float) -> float:
        t = self.params.tick_size
        return round(px / t) * t if t > 0 else px

    def on_book(self, event: BookEvent, book: BookView) -> list[QuoteEvent]:
        p = self.params
        sym = event.instrument
        best_bid, best_ask = event.best_bid, event.best_ask
        if not (best_bid == best_bid and best_ask == best_ask):     # NaN book → no quote
            return []

        sigma = self._sigma(sym, event.mid)
        q = book.position(sym)

        # 1. Fair value: microprice nudged by multi-level imbalance, clamped inside the touch.
        halfspread = max(best_ask - best_bid, 0.0) / 2.0
        s = event.microprice + p.obi_alpha * event.obi * halfspread
        s = min(max(s, best_bid), best_ask)

        # 2. Reservation price (inventory skew) and optimal spread (width).
        risk = p.gamma * sigma * sigma * p.horizon
        r = s - q * risk
        spread = risk + (2.0 / p.gamma) * math.log1p(p.gamma / p.kappa)
        half = spread / 2.0

        # 3. Inventory soften: widen the side that would *increase* |inventory|.
        bid_half = ask_half = half
        if p.inv_soften and p.q_max > 0:
            lean = abs(q) / p.q_max
            if q > 0:                       # long → discourage buying more
                bid_half = half * (1.0 + lean)
            elif q < 0:                     # short → discourage selling more
                ask_half = half * (1.0 + lean)

        bid_px: float | None = r - bid_half
        ask_px: float | None = r + ask_half

        # 4. Never quote through the touch.
        if p.join_only:
            bid_px = min(bid_px, best_bid)
            ask_px = max(ask_px, best_ask)
        bid_px = self._round(bid_px)
        ask_px = self._round(ask_px)

        # 5. Hard inventory limits → one-sided quoting.
        if q >= p.q_max:
            bid_px = None
        if q <= -p.q_max:
            ask_px = None

        return [QuoteEvent(timestamp=event.timestamp, instrument=sym,
                           bid_px=bid_px, ask_px=ask_px,
                           bid_size=p.quote_size, ask_size=p.quote_size)]
