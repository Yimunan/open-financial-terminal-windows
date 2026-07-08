"""A starter set of Alpha101 formulas (Kakushadze 2015), expressed over MarketPanels using
the operator vocabulary. Each is a ``Factor`` (direction = +1 — the formula already encodes
its sign), so they flow straight into ``factors.evaluation`` (IC / quantile / decay).

This is ~8 of the 101, chosen to exercise the full operator set (correlation, covariance,
ts_rank, ts_argmax, delta/sign, vwap, signedpower). Extend by adding more ``Alpha`` subclasses.
"""

from __future__ import annotations

import numpy as np

from qhfi.core.types import Panel
from qhfi.factors import operators as op
from qhfi.factors.market import Alpha, MarketPanels

ALL_ALPHAS: list[type[Alpha]] = []


def _register(cls: type[Alpha]) -> type[Alpha]:
    ALL_ALPHAS.append(cls)
    return cls


@_register
class Alpha101(Alpha):
    """(close - open) / ((high - low) + .001) — intraday momentum (the namesake)."""
    name = "alpha101"

    def expr(self, m: MarketPanels) -> Panel:
        return (m.close - m.open) / ((m.high - m.low) + 0.001)


@_register
class Alpha006(Alpha):
    """-1 * correlation(open, volume, 10) — price/volume divergence."""
    name = "alpha006"

    def expr(self, m: MarketPanels) -> Panel:
        return -1 * op.correlation(m.open, m.volume, 10)


@_register
class Alpha012(Alpha):
    """sign(delta(volume,1)) * (-1 * delta(close,1)) — volume-confirmed reversal."""
    name = "alpha012"

    def expr(self, m: MarketPanels) -> Panel:
        return np.sign(op.delta(m.volume, 1)) * (-1 * op.delta(m.close, 1))


@_register
class Alpha041(Alpha):
    """(high * low)^0.5 - vwap."""
    name = "alpha041"

    def expr(self, m: MarketPanels) -> Panel:
        return (m.high * m.low) ** 0.5 - m.vwap


@_register
class Alpha054(Alpha):
    """(-1 * (low - close) * open^5) / ((low - high) * close^5)."""
    name = "alpha054"

    def expr(self, m: MarketPanels) -> Panel:
        return (-1 * (m.low - m.close) * m.open ** 5) / ((m.low - m.high) * m.close ** 5)


@_register
class Alpha004(Alpha):
    """-1 * ts_rank(rank(low), 9) — short-term low-price reversal."""
    name = "alpha004"

    def expr(self, m: MarketPanels) -> Panel:
        return -1 * op.ts_rank(op.rank(m.low), 9)


@_register
class Alpha013(Alpha):
    """-1 * rank(covariance(rank(close), rank(volume), 5))."""
    name = "alpha013"

    def expr(self, m: MarketPanels) -> Panel:
        return -1 * op.rank(op.covariance(op.rank(m.close), op.rank(m.volume), 5))


@_register
class Alpha033(Alpha):
    """rank(-1 * (1 - open/close)) — i.e. rank(open/close - 1)."""
    name = "alpha033"

    def expr(self, m: MarketPanels) -> Panel:
        return op.rank(-1 * (1 - (m.open / m.close)))
