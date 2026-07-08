"""Scorecard OOS-robustness branches: supplied vs not, and the non-positive-IS-Sharpe guard
(the bug the walk-forward demo surfaced)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.backtest.engine import BacktestResult
from qhfi.evaluation.scorecard import Scorecard


def _result(returns: pd.Series) -> BacktestResult:
    empty = pd.Series(0.0, index=returns.index)
    return BacktestResult(
        equity_curve=(1 + returns).cumprod(), returns=returns,
        weights=pd.DataFrame(index=returns.index), turnover=empty, costs=empty, meta={},
        cash=empty, gross_exposure=empty, net_exposure=empty,
        commission=empty, slippage=empty, financing=empty, carry=empty,
        positions=pd.DataFrame(index=returns.index), trades=pd.DataFrame(),
    )


def _series(mean, n=252, seed=0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series(np.random.default_rng(seed).normal(mean, 0.01, n), index=idx)


def test_no_oos_supplied_notes_and_no_robustness_check():
    card = Scorecard().grade(_result(_series(0.001)))
    assert "oos_robustness" not in card.checks
    assert any("no OOS returns supplied" in n for n in card.notes)


def test_negative_is_sharpe_does_not_fabricate_robustness():
    res = _result(_series(-0.001))                 # losing strategy → IS Sharpe < 0
    card = Scorecard().grade(res, oos_returns=_series(-0.001, seed=1))
    assert "oos_robustness" not in card.checks      # guard: ratio undefined, not invented
    assert any("Sharpe ≤ 0" in n for n in card.notes)
    assert "oos_sharpe" in card.metrics             # still reported for inspection


def test_positive_is_sharpe_runs_robustness_check():
    res = _result(_series(0.002, seed=2))           # IS Sharpe > 0
    strong = Scorecard().grade(res, oos_returns=_series(0.002, seed=3))
    weak = Scorecard().grade(res, oos_returns=_series(-0.002, seed=4))
    assert strong.checks["oos_robustness"] is True
    assert weak.checks["oos_robustness"] is False
