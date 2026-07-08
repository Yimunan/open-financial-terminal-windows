"""In-process simulated paper broker — fills at the next available price with modeled
slippage, tracks cash/positions/equity. Asset-class agnostic; used for any market when you
don't want to depend on a venue's own paper environment.
"""

from __future__ import annotations

from qhfi.execution.base import Account, Broker, Order, Position


class PaperBroker(Broker):
    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self.cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._last_price: dict[str, float] = {}

    def mark(self, instrument_id: str, price: float) -> None:
        """Feed the latest close so equity/fills can be marked."""
        self._last_price[instrument_id] = price

    def get_account(self) -> Account:
        raise NotImplementedError("TODO: equity = cash + Σ qty*last_price")

    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def submit(self, order: Order) -> str:
        """Fill immediately at last price ± slippage; update cash & position."""
        raise NotImplementedError("TODO: simulate fill, update cash/position, return order id")
