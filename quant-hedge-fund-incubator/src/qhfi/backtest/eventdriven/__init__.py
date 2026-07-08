"""Event-driven backtester — a handler/queue alternative to the vectorized ``BacktestEngine``.

A timestamp-ordered event loop (``MarketEvent → SignalEvent → OrderEvent → FillEvent``) through
pluggable components — :class:`~qhfi.backtest.eventdriven.data.DataHandler`,
:class:`~qhfi.backtest.eventdriven.strategy.EventStrategy`,
:class:`~qhfi.backtest.eventdriven.portfolio.Portfolio`,
:class:`~qhfi.backtest.eventdriven.execution.ExecutionHandler` — that reuses the same cost / fill /
financing / sizing models and emits the same ``BacktestResult``. ``WeightStrategyAdapter`` runs any
existing vectorized ``Strategy`` on it; on dense daily data it is numerically equivalent to
``BacktestEngine``.
"""

from qhfi.backtest.eventdriven.data_book import BookReplayDataHandler
from qhfi.backtest.eventdriven.engine import (
    EventDrivenEngine,
    EventLoop,
    MarketMakingEngine,
    QuotingEventLoop,
)
from qhfi.backtest.eventdriven.events import (
    BookEvent,
    Event,
    FillEvent,
    MarketEvent,
    OrderEvent,
    QuoteEvent,
    SignalEvent,
    TradeEvent,
)
from qhfi.backtest.eventdriven.execution import ExecutionHandler, SimulatedExecutionHandler
from qhfi.backtest.eventdriven.matching import LimitOrderMatchingHandler
from qhfi.backtest.eventdriven.strategy import EventStrategy, QuotingStrategy, WeightStrategyAdapter

__all__ = [
    "EventDrivenEngine",
    "EventLoop",
    "MarketMakingEngine",
    "QuotingEventLoop",
    "Event",
    "MarketEvent",
    "SignalEvent",
    "OrderEvent",
    "FillEvent",
    "BookEvent",
    "QuoteEvent",
    "TradeEvent",
    "EventStrategy",
    "QuotingStrategy",
    "WeightStrategyAdapter",
    "ExecutionHandler",
    "SimulatedExecutionHandler",
    "LimitOrderMatchingHandler",
    "BookReplayDataHandler",
]
