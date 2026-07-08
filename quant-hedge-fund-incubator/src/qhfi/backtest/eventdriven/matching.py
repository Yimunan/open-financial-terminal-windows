"""Limit-order matching — the fill engine for the quoting market-maker.

Where ``SimulatedExecutionHandler`` fills a *market* order against a single reference price,
this holds **resting two-sided quotes** between heartbeats and decides when crossing flow
fills them. A passive fill earns the spread: it executes *at the quote price* (no slippage),
paying only the maker commission. Adverse selection emerges naturally — the mid usually
moves against the side that just filled — and is measured downstream by markout metrics.

Two fill models:
  * ``cross`` (default, deterministic): a resting bid fills when a later best-ask crosses it
    (a sweep to our price) or a trade prints at/through it. Used for tests and the base case.
  * ``intensity`` (seeded, stochastic): per-book fill hazard ``1 − exp(−A·e^{−κδ}·Δt)`` from
    calibrated ``(A, κ)`` — a sensitivity tool when no trade tape exists.

An optional queue model gates fills until volume *ahead of us* at our price level is depleted;
without per-order data this is the least trustworthy component, so it is a toggle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from qhfi.backtest.costs import BpsCostModel, CostModel
from qhfi.backtest.eventdriven.events import BookEvent, FillEvent, QuoteEvent, TradeEvent
from qhfi.core.types import Instrument


@dataclass
class _Resting:
    """One side of a resting quote and its queue position (volume ahead at our price)."""

    px: float
    size: float
    queue: float
    ref_mid: float       # mid when posted — the markout/adverse-selection reference


class LimitOrderMatchingHandler:
    """Match resting quotes against subsequent book/trade events into ``FillEvent``s."""

    def __init__(
        self,
        cost_model: CostModel | None = None,
        fill_model: Literal["cross", "intensity"] = "cross",
        queue_model: bool = True,
        arrival_a: float = 1.0,
        seed: int = 0,
        taker_cost_model: CostModel | None = None,
    ) -> None:
        # Default to a flat *maker* cost (1 bps) — passive fills are cheaper than the 10 bps
        # taker the CompositeCostModel charges crypto; a maker-rebate model can be passed in.
        self.cost_model = cost_model or BpsCostModel(1.0)
        # A *marketable* (crossing) quote pays the taker fee and crosses the spread. Default 10 bps.
        self.taker_cost_model = taker_cost_model or BpsCostModel(10.0)
        self.fill_model = fill_model
        self.queue_model = queue_model
        self.arrival_a = arrival_a
        self._rng = np.random.default_rng(seed)

        self._bid: dict[str, _Resting] = {}
        self._ask: dict[str, _Resting] = {}
        self._prev_book: dict[str, BookEvent] = {}
        self.n_quotes = 0

    # ── posting ──────────────────────────────────────────────────────────────────
    def post(self, quote: QuoteEvent, instrument: Instrument | None = None) -> list[FillEvent]:
        """Replace the resting quote, executing any **marketable** (crossing) side immediately.

        A passive side rests (queue seeded from the current book — joining sits behind the
        displayed size, improving the touch jumps the queue). A side priced through the opposing
        touch (``bid_px ≥ best_ask`` / ``ask_px ≤ best_bid``) is a *taker* order: it fills now at
        that touch, paying the taker fee + the spread it crossed (returned as ``FillEvent``s).
        Crossing fills need ``instrument`` for sizing/commission; without it the side just rests.
        """
        self.n_quotes += 1
        sym = quote.instrument
        book = self._prev_book.get(sym)
        mid = book.mid if book is not None else float("nan")
        fills: list[FillEvent] = []

        if quote.bid_px is not None and quote.bid_size > 0:
            if instrument is not None and book is not None and quote.bid_px >= book.best_ask:
                fills.append(self._taker_fill(quote.timestamp, sym, +quote.bid_size,
                                              book.best_ask, mid, instrument))
                self._bid.pop(sym, None)
            else:
                self._bid[sym] = _Resting(quote.bid_px, quote.bid_size,
                                          self._queue_ahead(book, "bid", quote.bid_px), mid)
        else:
            self._bid.pop(sym, None)

        if quote.ask_px is not None and quote.ask_size > 0:
            if instrument is not None and book is not None and quote.ask_px <= book.best_bid:
                fills.append(self._taker_fill(quote.timestamp, sym, -quote.ask_size,
                                              book.best_bid, mid, instrument))
                self._ask.pop(sym, None)
            else:
                self._ask[sym] = _Resting(quote.ask_px, quote.ask_size,
                                          self._queue_ahead(book, "ask", quote.ask_px), mid)
        else:
            self._ask.pop(sym, None)
        return fills

    def _queue_ahead(self, book: BookEvent | None, side: str, px: float) -> float:
        if book is None or not self.queue_model:
            return 0.0
        levels = book.bids if side == "bid" else book.asks
        touch = book.best_bid if side == "bid" else book.best_ask
        # Improving past the touch ⇒ no one ahead; otherwise queue behind displayed size at px.
        if (side == "bid" and px > touch) or (side == "ask" and px < touch):
            return 0.0
        return sum(sz for p, sz in levels if math.isclose(p, px, rel_tol=1e-9, abs_tol=1e-12))

    # ── matching ─────────────────────────────────────────────────────────────────
    def on_book(self, event: BookEvent, instrument: Instrument) -> list[FillEvent]:
        """Fill resting quotes against a new book. ``cross`` fills on a sweep through our
        price; ``intensity`` draws a probabilistic fill from quote distance to mid."""
        sym = event.instrument
        fills: list[FillEvent] = []

        # Queue depletion proxy: displayed size at our level shrinking ⇒ volume executed ahead.
        prev = self._prev_book.get(sym)
        if self.queue_model and prev is not None:
            self._deplete(self._bid.get(sym), prev, event, "bid")
            self._deplete(self._ask.get(sym), prev, event, "ask")

        if self.fill_model == "cross":
            b = self._bid.get(sym)
            if b is not None and b.queue <= 0 and event.best_ask <= b.px:
                fills.append(self._fill(event.timestamp, sym, +b.size, b, instrument))
                self._bid.pop(sym, None)
            a = self._ask.get(sym)
            if a is not None and a.queue <= 0 and event.best_bid >= a.px:
                fills.append(self._fill(event.timestamp, sym, -a.size, a, instrument))
                self._ask.pop(sym, None)
        else:  # intensity
            fills += self._intensity_fills(event, instrument)

        self._prev_book[sym] = event
        return fills

    def on_trade(self, event: TradeEvent, instrument: Instrument) -> list[FillEvent]:
        """Fill resting quotes against a trade print: a sell into our bid, a buy into our ask.
        Consumes queue-ahead first, then fills (possibly partially)."""
        sym = event.instrument
        fills: list[FillEvent] = []
        size = event.size

        b = self._bid.get(sym)
        if b is not None and event.price <= b.px and size > 0:
            consumed = min(b.queue, size)
            b.queue -= consumed
            size -= consumed
            if b.queue <= 0 and size > 0:
                qty = min(b.size, size)
                fills.append(self._fill(event.timestamp, sym, +qty, b, instrument, qty))
                b.size -= qty
                if b.size <= 1e-12:
                    self._bid.pop(sym, None)

        a = self._ask.get(sym)
        size = event.size
        if a is not None and event.price >= a.px and size > 0:
            consumed = min(a.queue, size)
            a.queue -= consumed
            size -= consumed
            if a.queue <= 0 and size > 0:
                qty = min(a.size, size)
                fills.append(self._fill(event.timestamp, sym, -qty, a, instrument, qty))
                a.size -= qty
                if a.size <= 1e-12:
                    self._ask.pop(sym, None)
        return fills

    # ── helpers ──────────────────────────────────────────────────────────────────
    def _deplete(self, rest: _Resting | None, prev: BookEvent, cur: BookEvent, side: str) -> None:
        if rest is None or rest.queue <= 0:
            return
        disp = lambda bk: sum(sz for p, sz in (bk.bids if side == "bid" else bk.asks)
                              if math.isclose(p, rest.px, rel_tol=1e-9, abs_tol=1e-12))
        drop = disp(prev) - disp(cur)
        if drop > 0:
            rest.queue = max(0.0, rest.queue - drop)

    def _fill(self, ts, sym: str, delta_units: float, rest: _Resting,
              instrument: Instrument, qty: float | None = None) -> FillEvent:
        units = qty if qty is not None else abs(delta_units)
        mult = instrument.contract_multiplier
        notional = units * rest.px * mult
        commission = self.cost_model.cost(notional, instrument, rest.px)
        return FillEvent(
            timestamp=ts, instrument=sym, delta_units=delta_units, fill_price=rest.px,
            ref_price=rest.ref_mid, commission=commission, slippage=0.0,
            margined=instrument.is_margined,
        )

    def _taker_fill(self, ts, sym: str, delta_units: float, touch_px: float, ref_mid: float,
                    instrument: Instrument) -> FillEvent:
        """A marketable order: fill at the opposing ``touch_px``, paying the taker fee + the spread
        crossed (the touch-vs-mid distance is booked as slippage, like an aggressive market order)."""
        units = abs(delta_units)
        mult = instrument.contract_multiplier
        commission = self.taker_cost_model.cost(units * touch_px * mult, instrument, touch_px)
        slippage = abs(touch_px - ref_mid) * units * mult if ref_mid == ref_mid else 0.0
        return FillEvent(
            timestamp=ts, instrument=sym, delta_units=delta_units, fill_price=touch_px,
            ref_price=ref_mid, commission=commission, slippage=slippage,
            margined=instrument.is_margined,
        )

    def _intensity_fills(self, event: BookEvent, instrument: Instrument) -> list[FillEvent]:
        sym = event.instrument
        out: list[FillEvent] = []
        for store, sign in ((self._bid, +1), (self._ask, -1)):
            rest = store.get(sym)
            if rest is None:
                continue
            delta = abs(event.mid - rest.px)
            kappa = max(getattr(self, "kappa", 1.5), 1e-9)
            hazard = self.arrival_a * math.exp(-kappa * delta)
            if self._rng.random() < 1.0 - math.exp(-hazard):
                out.append(self._fill(event.timestamp, sym, sign * rest.size, rest, instrument))
                store.pop(sym, None)
        return out
