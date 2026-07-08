"""PaperQuotingLoop — drive a market-making strategy live (paper) against a streaming book.

The incubator's lifecycle is research → backtest → **paper**; this is the paper stage for the
quoting strategies. It consumes a stream of ``BookEvent``s (a live ccxt-pro ``watch_order_book``
feed, or a recorded/synthetic replay), fills the strategy's resting quotes against each update via
the same ``LimitOrderMatchingHandler`` the backtest uses, and tracks inventory/cash/PnL through the
shared ``Portfolio`` accounting — so paper and backtest can't drift. On top of the backtest engine
it adds the two things a live loop needs: **risk gates** (a hard inventory cap that overrides the
strategy and a drawdown **kill-switch** that cancels quotes and stops) and an incremental
``on_book`` + ``state()`` for monitoring.

Fills are *simulated* against the real book (no orders leave the machine) — honest paper trading.
A real-money venue would swap the matcher for a broker that posts/cancels actual limit orders.
"""

from __future__ import annotations

from dataclasses import dataclass

from qhfi.backtest.costs import BpsCostModel, CostModel
from qhfi.backtest.engine import BacktestResult
from qhfi.backtest.eventdriven.events import BookEvent, QuoteEvent, TradeEvent
from qhfi.backtest.eventdriven.matching import LimitOrderMatchingHandler
from qhfi.backtest.eventdriven.portfolio import Portfolio
from qhfi.backtest.eventdriven.strategy import QuotingStrategy
from qhfi.core.types import Instrument


@dataclass
class QuotingRiskLimits:
    """Live guardrails on top of the strategy's own inventory limit."""

    max_inventory: float = 1e18          # hard |inventory| cap enforced by the loop
    max_drawdown_kill: float = 1.0       # fraction of peak equity; below it → halt (1.0 = off)


@dataclass
class QuotingState:
    """A snapshot of the loop after a heartbeat — what a live monitor renders."""

    timestamp: object
    mid: float
    inventory: float
    cash: float
    equity: float
    pnl: float
    n_fills: int
    n_quotes: int
    halted: bool
    reason: str


class PaperQuotingLoop:
    """Run a ``QuotingStrategy`` against a live/replayed book with paper fills + risk gates."""

    def __init__(
        self,
        strategy: QuotingStrategy,
        instrument: Instrument,
        *,
        cost_model: CostModel | None = None,
        initial_equity: float = 100_000.0,
        risk: QuotingRiskLimits | None = None,
        matching: LimitOrderMatchingHandler | None = None,
    ) -> None:
        self.strategy = strategy
        self.instr = instrument
        self.sym = instrument.id
        self.matching = matching or LimitOrderMatchingHandler(
            cost_model=cost_model or BpsCostModel(1.0), queue_model=False)
        self.portfolio = Portfolio({self.sym: instrument}, initial_equity=initial_equity,
                                   universe_name="paper")
        self.risk = risk or QuotingRiskLimits()

        self.halted = False
        self.reason = ""
        self._peak = initial_equity
        self._last_ts = None

    # ── one heartbeat (live-friendly) ────────────────────────────────────────────
    def on_book(self, event: BookEvent) -> QuotingState:
        """Process one book update: settle resting quotes, re-mark, gate risk, re-quote."""
        if self._last_ts is None or event.timestamp != self._last_ts:
            if self._last_ts is not None:
                self.portfolio.record(self._last_ts)
            self.portfolio.open_heartbeat()
            self._last_ts = event.timestamp

        for fill in self.matching.on_book(event, self.instr):
            self.portfolio.apply_fill(fill)
            self.portfolio.add_traded_notional(
                abs(fill.delta_units) * fill.fill_price * self.instr.contract_multiplier)
        self.portfolio.mark(self.sym, event.mid)

        equity = self.portfolio.equity()
        self._peak = max(self._peak, equity)
        if not self.halted and equity < (1.0 - self.risk.max_drawdown_kill) * self._peak:
            self.halted, self.reason = True, "drawdown kill-switch"

        if self.halted:
            self.matching.post(QuoteEvent(timestamp=event.timestamp, instrument=self.sym))  # cancel all
        else:
            inv = self.portfolio.position(self.sym)
            for quote in self.strategy.on_book(event, self.portfolio):
                for fill in self.matching.post(self._cap(quote, inv), self.instr):  # marketable side fills now
                    self.portfolio.apply_fill(fill)
                    self.portfolio.add_traded_notional(
                        abs(fill.delta_units) * fill.fill_price * self.instr.contract_multiplier)
        return self.state(event)

    def on_trade(self, event: TradeEvent) -> None:
        """Optional: fill resting quotes against a live trade print between book updates."""
        if self.halted:
            return
        for fill in self.matching.on_trade(event, self.instr):
            self.portfolio.apply_fill(fill)
            self.portfolio.add_traded_notional(
                abs(fill.delta_units) * fill.fill_price * self.instr.contract_multiplier)

    def run(self, events) -> list[QuotingState]:
        """Drive the loop over a finite iterable of ``BookEvent`` (replay / backtest-as-paper)."""
        states = []
        for ev in events:
            if isinstance(ev, BookEvent):
                states.append(self.on_book(ev))
            elif isinstance(ev, TradeEvent):
                self.on_trade(ev)
        if self._last_ts is not None:
            self.portfolio.record(self._last_ts)
        return states

    # ── helpers ──────────────────────────────────────────────────────────────────
    def _cap(self, quote: QuoteEvent, inv: float) -> QuoteEvent:
        """Loop-level hard inventory cap: suppress the side that would grow |inventory| further."""
        bid = None if inv >= self.risk.max_inventory else quote.bid_px
        ask = None if inv <= -self.risk.max_inventory else quote.ask_px
        if bid is quote.bid_px and ask is quote.ask_px:
            return quote
        return QuoteEvent(timestamp=quote.timestamp, instrument=quote.instrument,
                          bid_px=bid, ask_px=ask, bid_size=quote.bid_size, ask_size=quote.ask_size)

    def state(self, event: BookEvent) -> QuotingState:
        equity = self.portfolio.equity()
        return QuotingState(
            timestamp=event.timestamp, mid=event.mid,
            inventory=self.portfolio.position(self.sym), cash=self.portfolio.cash,
            equity=equity, pnl=equity - self.portfolio.initial_equity,
            n_fills=len(self.portfolio._trades), n_quotes=self.matching.n_quotes,
            halted=self.halted, reason=self.reason,
        )

    def result(self) -> BacktestResult:
        """The full equity/positions/trades record (same shape as a backtest)."""
        return self.portfolio.result()
