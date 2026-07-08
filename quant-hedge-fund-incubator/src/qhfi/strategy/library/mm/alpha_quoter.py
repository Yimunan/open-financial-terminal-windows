"""AlphaQuoterMM — an inventory-aware quoter with a calibrated predictive OBI overlay.

The structural weakness of a passive market-maker is **adverse selection**: it gets filled right
before the price moves against the fill. Order-book imbalance predicts the short-horizon mid move
(``microstructure.forward_return_on_obi`` calibrates *how much*, in bps per unit OBI), so this
strategy shifts its fair value by that predicted drift — ``center = microprice + mid·αbps·OBI`` —
quoting **ahead** of the imbalance move: when buy pressure builds it lifts both quotes (declining
to sell cheap into the up-move, leaning to buy and ride it). That directional defense is what turns
captured spread into net edge once a real signal exists. It keeps the same linear inventory skew as
``LinearInventoryMM``; with ``alpha_bps=0`` it reduces to that quoter.
"""

from __future__ import annotations

import numpy as np

from qhfi.backtest.eventdriven.events import BookEvent, QuoteEvent
from qhfi.backtest.eventdriven.strategy import BookView, QuotingStrategy
from qhfi.strategy.base import StrategyParams


class AlphaQuoterMMParams(StrategyParams):
    half_spread_bps: float = 5.0     # base half-spread (bps of mid)
    skew_bps: float = 8.0            # inventory skew at ±q_max (bps of mid)
    alpha_bps: float = 0.0           # predicted forward return per unit OBI (bps) — from the fit
    alpha_gain: float = 1.0          # shrink the forecast toward 0 for risk control (0..1)
    q_max: float = 100.0             # hard inventory limit (units) → one-sided quoting
    quote_size: float = 1.0
    join_only: bool = True           # never quote through the touch (passive sides)
    tick_size: float = 0.0           # round quotes to this tick; 0 → no rounding
    # Taker mode: when the predicted edge beats the cost of crossing (½ the market spread + the
    # taker fee) by this margin (bps), cross to *capture* the move instead of only withdrawing.
    take_threshold_bps: float = 0.0  # 0 → never take (pure maker)
    taker_fee_bps: float = 10.0      # the taker fee the matcher will charge a marketable order
    take_size: float = 0.0           # size to take; 0 → quote_size


class AlphaQuoterMM(QuotingStrategy):
    """Microprice + calibrated OBI drift fair value, with linear inventory skew."""

    name = "AlphaQuoterMM"
    params_model = AlphaQuoterMMParams

    def __init__(self, params: AlphaQuoterMMParams | None = None) -> None:
        self.params = params or AlphaQuoterMMParams()

    def _round(self, px: float) -> float:
        t = self.params.tick_size
        return round(px / t) * t if t > 0 else px

    def on_book(self, event: BookEvent, book: BookView) -> list[QuoteEvent]:
        p = self.params
        best_bid, best_ask = event.best_bid, event.best_ask
        if not (best_bid == best_bid and best_ask == best_ask):     # NaN book → no quote
            return []

        mid = event.mid
        half = mid * p.half_spread_bps / 1e4
        # Calibrated directional fair value: shift by the predicted OBI-driven drift.
        drift = mid * p.alpha_bps * p.alpha_gain * event.obi / 1e4
        center = event.microprice + drift

        q = book.position(event.instrument)
        if p.q_max > 0:
            lean = float(np.clip(q / p.q_max, -1.0, 1.0))
            center -= lean * mid * p.skew_bps / 1e4                  # flatten inventory toward zero

        bid_px: float | None = center - half
        ask_px: float | None = center + half
        if p.join_only:
            bid_px = min(bid_px, best_bid)
            ask_px = max(ask_px, best_ask)
        bid_px, ask_px = self._round(bid_px), self._round(ask_px)
        bid_size = ask_size = p.quote_size

        # Taker mode: if the predicted edge beats the cost of crossing, take instead of withdraw.
        if p.take_threshold_bps > 0 and event.obi != 0.0:
            edge_bps = p.alpha_bps * p.alpha_gain * abs(event.obi)
            cross_cost_bps = (best_ask - best_bid) / mid / 2.0 * 1e4 + p.taker_fee_bps
            if edge_bps - cross_cost_bps > p.take_threshold_bps:
                take = p.take_size or p.quote_size
                if event.obi > 0:                       # predict up → cross to BUY at the ask
                    bid_px, bid_size = best_ask, take
                else:                                   # predict down → cross to SELL at the bid
                    ask_px, ask_size = best_bid, take

        if q >= p.q_max:
            bid_px = None
        if q <= -p.q_max:
            ask_px = None
        return [QuoteEvent(timestamp=event.timestamp, instrument=event.instrument,
                           bid_px=bid_px, ask_px=ask_px,
                           bid_size=bid_size, ask_size=ask_size)]
