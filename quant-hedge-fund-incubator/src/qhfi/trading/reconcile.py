"""Turn a target-weight row into broker orders given the current account.

Pure function: (target weights, account equity, current positions, latest prices,
instruments) → list of orders that move current holdings toward target. Rounds to
lot/contract size and drops dust trades below a threshold.

This decides *how many* shares to trade to track the target; it is intentionally tax-agnostic
(which lots a sale consumes doesn't change the share delta — that lives in ``tax.apply_orders``).
"""

from __future__ import annotations

from qhfi.core.types import Instrument
from qhfi.execution.base import Account, Order, OrderSide


def _round_to_lot(units: float, lot: float) -> float:
    """Round a unit count to the instrument's lot size (mirrors backtest.engine)."""
    if lot <= 0:
        return units
    return round(units / lot) * lot


def diff_to_orders(
    target_weights: dict[str, float],
    account: Account,
    prices: dict[str, float],
    instruments: dict[str, Instrument],
    min_trade_notional: float = 25.0,
) -> list[Order]:
    """Compute target_qty = weight*equity/price (×contract_multiplier aware), subtract
    current qty, round to lot_size, emit BUY/SELL orders above ``min_trade_notional``.
    """
    orders: list[Order] = []
    for iid, weight in target_weights.items():
        ins = instruments.get(iid)
        price = prices.get(iid)
        if ins is None or price is None or price <= 0:
            continue

        mult = ins.contract_multiplier
        denom = price * mult
        if denom <= 0:
            continue

        target_qty = weight * account.equity / denom
        current_qty = account.positions[iid].quantity if iid in account.positions else 0.0
        delta = _round_to_lot(target_qty - current_qty, ins.lot_size)
        if delta == 0:
            continue
        if abs(delta) * denom < min_trade_notional:   # dust
            continue

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        orders.append(Order(instrument_id=iid, side=side, quantity=abs(delta)))
    return orders
