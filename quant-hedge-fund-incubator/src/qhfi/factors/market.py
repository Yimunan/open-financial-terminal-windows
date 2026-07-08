"""Multi-field market panels + the Alpha factor base.

Alpha101 formulas need open/high/low/close/volume/vwap/returns — more than the single close
panel the plain ``Factor`` contract passes. ``MarketPanels`` bundles the per-field wide panels
(assembled from the lake), and ``Alpha`` is a ``Factor`` that computes from them.

``vwap`` is approximated as the typical price (high+low+close)/3 — we don't store true
intraday VWAP. ``adv(d)`` is rolling mean dollar volume.
"""

from __future__ import annotations

from dataclasses import dataclass

from qhfi.core.types import Panel, Universe
from qhfi.data.base import DataStore
from qhfi.factors.base import Factor, FactorParams


@dataclass
class MarketPanels:
    open: Panel
    high: Panel
    low: Panel
    close: Panel
    volume: Panel

    @property
    def vwap(self) -> Panel:
        return (self.high + self.low + self.close) / 3.0

    @property
    def returns(self) -> Panel:
        return self.close.pct_change()

    def adv(self, d: int) -> Panel:
        return (self.close * self.volume).rolling(d).mean()

    @classmethod
    def from_store(cls, store: DataStore, universe: Universe) -> MarketPanels:
        close = store.load_panel(universe.instruments, "close")
        def field(name: str) -> Panel:
            return store.load_panel(universe.instruments, name).reindex(
                index=close.index, columns=close.columns)
        return cls(open=field("open"), high=field("high"), low=field("low"),
                   close=close, volume=field("volume"))


class Alpha(Factor):
    """A Factor whose score is an Alpha101 expression over ``MarketPanels``. Construct with
    the panels (it carries data, like FundamentalFactor) rather than zero-arg."""

    params_model = FactorParams

    def __init__(self, panels: MarketPanels, params: FactorParams | None = None) -> None:
        super().__init__(params)
        self.m = panels

    def expr(self, m: MarketPanels) -> Panel:
        raise NotImplementedError

    def compute(self, prices: Panel, universe: Universe) -> Panel:
        # alphas read their own panels; reindex to the evaluation grid for alignment
        return self.expr(self.m).reindex(index=prices.index, columns=prices.columns)
