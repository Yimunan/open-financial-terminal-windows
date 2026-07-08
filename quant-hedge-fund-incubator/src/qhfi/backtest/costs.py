"""Transaction-cost (commission) models — the main place asset-class differences re-enter
the engine.

These model **commission only**. Price slippage is handled separately by the engine's
``SlippageModel`` (it moves the fill price), so cost models must not also charge slippage or
it would be double-counted. The engine calls ``cost(traded_notional, instrument, price)`` per
fill and subtracts it from cash; ``CompositeCostModel`` dispatches by ``asset_class``.
"""

from __future__ import annotations

from typing import Protocol

from qhfi.core.types import AssetClass, Instrument


class CostModel(Protocol):
    def cost(self, traded_notional: float, instrument: Instrument, price: float) -> float:
        """Commission (quote currency) for trading ``traded_notional`` at ``price``."""
        ...


class BpsCostModel:
    """Flat bps of traded notional — good default for crypto/FX (taker fee)."""

    def __init__(self, bps: float = 10.0) -> None:
        self.bps = bps

    def cost(self, traded_notional: float, instrument: Instrument, price: float) -> float:
        return abs(traded_notional) * self.bps / 1e4


class EquityCostModel:
    """Per-share commission with a per-ticket minimum (US equities)."""

    def __init__(self, per_share: float = 0.005, min_ticket: float = 1.0) -> None:
        self.per_share, self.min_ticket = per_share, min_ticket

    def cost(self, traded_notional: float, instrument: Instrument, price: float) -> float:
        denom = price * instrument.contract_multiplier
        if denom <= 0:
            return self.min_ticket
        shares = abs(traded_notional) / denom
        return max(self.min_ticket, shares * self.per_share)


class FuturesCostModel:
    """Flat commission per contract (futures / commodities)."""

    def __init__(self, per_contract: float = 2.0) -> None:
        self.per_contract = per_contract

    def cost(self, traded_notional: float, instrument: Instrument, price: float) -> float:
        denom = price * instrument.contract_multiplier
        if denom <= 0:
            return 0.0
        contracts = abs(traded_notional) / denom
        return contracts * self.per_contract


class CompositeCostModel:
    """Dispatches commission by asset class, with a bps fallback for any class not configured
    (futures-form instruments are best served by FuturesCostModel regardless of asset class —
    wire that per-universe when contract specs are known)."""

    def __init__(
        self,
        by_class: dict[AssetClass, CostModel] | None = None,
        default: CostModel | None = None,
    ) -> None:
        self.by_class = by_class or {
            AssetClass.CRYPTO: BpsCostModel(10.0),
            AssetClass.EQUITY: EquityCostModel(),
            AssetClass.FX: BpsCostModel(2.0),
            AssetClass.RATES: BpsCostModel(1.0),
            AssetClass.CREDIT: BpsCostModel(3.0),
            AssetClass.COMMODITY: FuturesCostModel(),
        }
        self.default = default or BpsCostModel(5.0)

    def cost(self, traded_notional: float, instrument: Instrument, price: float) -> float:
        return self.by_class.get(instrument.asset_class, self.default).cost(
            traded_notional, instrument, price
        )
