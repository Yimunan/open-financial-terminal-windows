"""Tests for walk-forward OOS: fold generation, non-overlapping test windows, and stitched
OOS returns. Offline + deterministic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.validation import WalkForwardConfig, concat_oos, walk_forward
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.factors.library import MomentumFactor
from qhfi.strategy.library.factor_strategy import FactorStrategy, FactorStrategyParams


def _setup():
    dates = pd.date_range("2024-01-01", periods=420, freq="D", tz="UTC")
    drifts = np.linspace(-0.001, 0.001, 6)
    prices = pd.DataFrame(
        {f"A{i}": 100 * np.cumprod(1 + np.full(420, d)) for i, d in enumerate(drifts)},
        index=dates,
    )
    uni = Universe(name="t", instruments=[
        Instrument(id=f"A{i}", asset_class=AssetClass.CRYPTO) for i in range(6)
    ])
    strat = FactorStrategy([MomentumFactor()], params=FactorStrategyParams(quantile=0.34))
    return strat, prices, uni


def test_walk_forward_produces_disjoint_oos_windows():
    strat, prices, uni = _setup()
    cfg = WalkForwardConfig(train_days=200, test_days=50, step_days=50, purge_days=5)
    folds = walk_forward(strat, prices, uni, BacktestEngine(), cfg)
    assert len(folds) >= 3

    # each fold's window matches test_days and sits strictly after the previous one
    last_end = None
    for f in folds:
        assert len(f.returns) == cfg.test_days
        start = f.returns.index.min()
        if last_end is not None:
            assert start > last_end
        last_end = f.returns.index.max()


def test_concat_oos_is_contiguous_and_within_range():
    strat, prices, uni = _setup()
    cfg = WalkForwardConfig(train_days=200, test_days=50, step_days=50, purge_days=5)
    folds = walk_forward(strat, prices, uni, BacktestEngine(), cfg)
    oos = concat_oos(folds)

    assert not oos.index.has_duplicates
    assert oos.index.is_monotonic_increasing
    assert len(oos) == sum(len(f.returns) for f in folds)
    assert oos.index.min() >= prices.index[cfg.train_days]   # all OOS, never in the first train block


def test_empty_when_history_too_short():
    strat, prices, uni = _setup()
    cfg = WalkForwardConfig(train_days=1000, test_days=50, step_days=50, purge_days=5)
    assert walk_forward(strat, prices, uni, BacktestEngine(), cfg) == []
    assert concat_oos([]).empty
