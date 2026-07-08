"""The event loop and the ``EventDrivenEngine`` facade.

``EventLoop`` is a single time-ordered priority queue: it pulls ``MarketEvent``s from the data
handler and dispatches every event to its handler, draining all of a timestamp's derived events
(orders → fills → signal stored → record) before the next bar. ``EventDrivenEngine`` wires the
components and exposes the same ``run(weights, prices, universe)`` signature as
``backtest.engine.BacktestEngine`` (so it slots into ``walk_forward`` and the scorecard flow),
plus ``run_strategy`` for native push strategies.
"""

from __future__ import annotations

import heapq
import itertools

from qhfi.backtest.costs import CostModel
from qhfi.backtest.engine import BacktestResult, ExecutionConfig
from qhfi.backtest.eventdriven.data import DataHandler, PanelDataHandler
from qhfi.backtest.eventdriven.events import (
    FillEvent,
    MarketEvent,
    OrderEvent,
    RecordEvent,
    SignalEvent,
)
from qhfi.backtest.eventdriven.data_book import BookReplayDataHandler
from qhfi.backtest.eventdriven.events import BookEvent, TradeEvent
from qhfi.backtest.eventdriven.execution import ExecutionHandler, SimulatedExecutionHandler
from qhfi.backtest.eventdriven.matching import LimitOrderMatchingHandler
from qhfi.backtest.eventdriven.portfolio import Portfolio
from qhfi.backtest.eventdriven.strategy import EventStrategy, QuotingStrategy, WeightStrategyAdapter
from qhfi.backtest.fills import SlippageModel
from qhfi.backtest.financing import FinancingModel
from qhfi.core.types import Instrument, Panel, TargetWeights, Universe
from qhfi.portfolio.sizing import SizingModel


class EventLoop:
    """Drive a strategy through the data handler, portfolio, and execution handler in time order."""

    def __init__(
        self,
        data: DataHandler,
        strategy: EventStrategy,
        portfolio: Portfolio,
        execution: ExecutionHandler,
        instruments: dict[str, Instrument],
    ) -> None:
        self.data = data
        self.strategy = strategy
        self.portfolio = portfolio
        self.execution = execution
        self.instr = instruments

    def run(self) -> BacktestResult:
        heap: list[tuple] = []
        counter = itertools.count()

        def push(ev) -> None:
            heapq.heappush(heap, (ev.timestamp, ev.PRIORITY, next(counter), ev))

        stream = self.data.stream()
        first = next(stream, None)
        if first is None:
            return self.portfolio.result()
        push(first)

        while heap:
            _, _, _, ev = heapq.heappop(heap)
            if isinstance(ev, MarketEvent):
                for order in self.portfolio.on_market(ev):
                    push(order)
                for signal in self.strategy.on_market(ev, self.portfolio):
                    push(signal)
                push(RecordEvent(timestamp=ev.timestamp))
                nxt = next(stream, None)
                if nxt is not None:
                    push(nxt)
            elif isinstance(ev, OrderEvent):
                push(self.execution.execute(ev, self.instr[ev.instrument]))
            elif isinstance(ev, FillEvent):
                self.portfolio.apply_fill(ev)
            elif isinstance(ev, SignalEvent):
                self.portfolio.on_signal(ev)
            elif isinstance(ev, RecordEvent):
                self.portfolio.record(ev.timestamp)
        return self.portfolio.result()


class EventDrivenEngine:
    """Handler/queue backtester. Same constructor + ``run`` contract as ``BacktestEngine``."""

    def __init__(
        self,
        cost_model: CostModel | None = None,
        slippage: SlippageModel | None = None,
        financing: FinancingModel | None = None,
        sizing: SizingModel | None = None,
        execution: ExecutionConfig | None = None,
        initial_equity: float = 100_000.0,
    ) -> None:
        self.cost_model = cost_model
        self.slippage = slippage
        self.financing = financing
        self.sizing = sizing
        self.exec = execution
        self.initial_equity = initial_equity

    def _run(
        self,
        strategy: EventStrategy,
        prices: Panel,
        universe: Universe,
        open_prices: Panel | None,
        carry: Panel | None,
    ) -> BacktestResult:
        instruments = {c: universe.by_id(c) for c in prices.columns}
        portfolio = Portfolio(
            instruments, sizing=self.sizing, financing=self.financing, execution=self.exec,
            initial_equity=self.initial_equity, carry=carry, universe_name=universe.name,
        )
        execution = SimulatedExecutionHandler(self.cost_model, self.slippage)
        loop = EventLoop(PanelDataHandler(prices, open_prices), strategy, portfolio, execution, instruments)
        return loop.run()

    def run(
        self,
        weights: TargetWeights,
        prices: Panel,
        universe: Universe,
        open_prices: Panel | None = None,
        carry: Panel | None = None,
    ) -> BacktestResult:
        """Drop-in for ``BacktestEngine.run`` — replays a precomputed weights panel as signals."""
        return self._run(WeightStrategyAdapter(weights), prices, universe, open_prices, carry)

    def run_strategy(
        self,
        strategy: EventStrategy,
        prices: Panel,
        universe: Universe,
        open_prices: Panel | None = None,
        carry: Panel | None = None,
    ) -> BacktestResult:
        """Drive a native push ``EventStrategy`` (reacts to each bar)."""
        return self._run(strategy, prices, universe, open_prices, carry)


class QuotingEventLoop:
    """Drive a ``QuotingStrategy`` over recorded books through the matching handler.

    Unlike ``EventLoop`` there is no priority heap: the data handler already emits a single
    time-ordered stream, and fills are produced *synchronously* inside the matcher (no derived
    events to schedule). Per heartbeat (one timestamp): settle resting quotes against this
    event, re-mark inventory at the new mid, then ask the strategy for fresh quotes. The book
    is recorded once per timestamp, after every instrument/trade at that timestamp has settled.
    """

    def __init__(
        self,
        data: BookReplayDataHandler,
        strategy: QuotingStrategy,
        portfolio: Portfolio,
        matching: LimitOrderMatchingHandler,
        instruments: dict[str, Instrument],
    ) -> None:
        self.data = data
        self.strategy = strategy
        self.portfolio = portfolio
        self.matching = matching
        self.instr = instruments

    def run(self) -> BacktestResult:
        last_ts = None
        for ev in self.data.stream():
            if ev.timestamp != last_ts:
                if last_ts is not None:
                    self.portfolio.record(last_ts)
                self.portfolio.open_heartbeat()
                last_ts = ev.timestamp

            instrument = self.instr[ev.instrument]
            if isinstance(ev, BookEvent):
                for fill in self.matching.on_book(ev, instrument):
                    self.portfolio.apply_fill(fill)
                    self.portfolio.add_traded_notional(
                        abs(fill.delta_units) * fill.fill_price * instrument.contract_multiplier)
                self.portfolio.mark(ev.instrument, ev.mid)
                for quote in self.strategy.on_book(ev, self.portfolio):
                    for fill in self.matching.post(quote, instrument):   # marketable side fills now
                        self.portfolio.apply_fill(fill)
                        self.portfolio.add_traded_notional(
                            abs(fill.delta_units) * fill.fill_price * instrument.contract_multiplier)
            elif isinstance(ev, TradeEvent):
                for fill in self.matching.on_trade(ev, instrument):
                    self.portfolio.apply_fill(fill)
                    self.portfolio.add_traded_notional(
                        abs(fill.delta_units) * fill.fill_price * instrument.contract_multiplier)

        if last_ts is not None:
            self.portfolio.record(last_ts)
        return self.portfolio.result()


class MarketMakingEngine:
    """Quoting-engine facade — the market-maker counterpart of ``EventDrivenEngine``.

    Separate from ``EventDrivenEngine`` because the input shape (recorded books, not a weights
    panel) and the loop ordering (fills settle against resting state *before* the strategy
    re-quotes) differ. Returns the same ``BacktestResult`` so the scorecard/tearsheet/metrics
    stack works unchanged; MM-specific diagnostics live in ``evaluation.mm_metrics``.
    """

    def __init__(
        self,
        cost_model: CostModel | None = None,
        initial_equity: float = 100_000.0,
        fill_model: str = "cross",
        queue_model: bool = True,
        levels: int = 10,
        obi_decay: float = 0.0,
        seed: int = 0,
        taker_cost_model: CostModel | None = None,
    ) -> None:
        self.cost_model = cost_model
        self.initial_equity = initial_equity
        self.fill_model = fill_model
        self.queue_model = queue_model
        self.levels = levels
        self.obi_decay = obi_decay
        self.seed = seed
        self.taker_cost_model = taker_cost_model

    def run_quoting(
        self,
        strategy: QuotingStrategy,
        books: dict[str, "Panel"],
        universe: Universe,
        trades: dict[str, "Panel"] | None = None,
    ) -> BacktestResult:
        """Replay recorded L2 ``books`` (long-format per symbol) through ``strategy``."""
        instruments = {sym: universe.by_id(sym) for sym in books}
        portfolio = Portfolio(instruments, initial_equity=self.initial_equity,
                              universe_name=universe.name)
        matching = LimitOrderMatchingHandler(
            cost_model=self.cost_model, fill_model=self.fill_model,  # type: ignore[arg-type]
            queue_model=self.queue_model, seed=self.seed, taker_cost_model=self.taker_cost_model)
        data = BookReplayDataHandler(books, trades=trades, levels=self.levels, decay=self.obi_decay)
        result = QuotingEventLoop(data, strategy, portfolio, matching, instruments).run()
        result.meta["engine"] = "market_making"
        result.meta["n_quotes"] = matching.n_quotes
        result.meta["fill_model"] = self.fill_model
        return result
