"""Broker abstraction.

Only **paper** implementations are provided in this scope. The protocol intentionally
mirrors what a live adapter would need, so promotion to live is a new implementation behind
the same interface plus a risk-gated manual switch — not a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    instrument_id: str
    side: OrderSide
    quantity: float            # in instrument units (shares/contracts/coins)
    type: str = "market"       # market | limit
    limit_price: float | None = None


@dataclass
class Position:
    instrument_id: str
    quantity: float
    avg_price: float


@dataclass
class Account:
    equity: float
    cash: float
    positions: dict[str, Position]


class Broker(Protocol):
    def get_account(self) -> Account: ...
    def get_positions(self) -> dict[str, Position]: ...
    def submit(self, order: Order) -> str:
        """Submit an order; return a broker order id."""
        ...
