"""Execution handlers — turn an ``OrderEvent`` into a ``FillEvent``.

This is the framework's main extension seam: swap in a different fill model (latency, partial
fills, market-impact) without touching the loop or the portfolio. ``SimulatedExecutionHandler``
reproduces the vectorized engine's fill exactly: adverse slippage moves the *price* (so it also
re-marks the new position's PnL), and commission is charged per asset class.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from qhfi.backtest.costs import CompositeCostModel, CostModel
from qhfi.backtest.eventdriven.events import FillEvent, OrderEvent
from qhfi.backtest.fills import SlippageModel
from qhfi.core.types import Instrument


@runtime_checkable
class ExecutionHandler(Protocol):
    def execute(self, order: OrderEvent, instrument: Instrument) -> FillEvent:
        ...


class SimulatedExecutionHandler:
    """Adverse-slippage fill + per-asset-class commission (matches ``backtest.engine``)."""

    def __init__(self, cost_model: CostModel | None = None, slippage: SlippageModel | None = None) -> None:
        self.cost_model = cost_model or CompositeCostModel()
        self.slippage = slippage or SlippageModel()

    def execute(self, order: OrderEvent, instrument: Instrument) -> FillEvent:
        side = 1 if order.delta_units > 0 else -1
        fill = self.slippage.fill_price(order.ref_price, side)
        mult = instrument.contract_multiplier
        notional_traded = abs(order.delta_units) * fill * mult
        commission = self.cost_model.cost(notional_traded, instrument, fill)
        slip_cost = abs(order.delta_units) * abs(fill - order.ref_price) * mult
        return FillEvent(
            timestamp=order.timestamp, instrument=order.instrument, delta_units=order.delta_units,
            fill_price=fill, ref_price=order.ref_price, commission=commission, slippage=slip_cost,
            margined=instrument.is_margined,
        )
