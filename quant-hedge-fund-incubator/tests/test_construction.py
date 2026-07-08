"""Portfolio construction: dollar-neutrality, gross scaling, position caps, that smoothing
cuts turnover, and that vol-targeting hits the target risk level."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.portfolio.construction import ConstructionConfig, PortfolioConstructor


def _score(n=300, k=8, seed=0):
    idx = pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")
    cols = [f"S{i}" for i in range(k)]
    return pd.DataFrame(np.random.default_rng(seed).normal(size=(n, k)), index=idx, columns=cols)


def test_dollar_neutral_and_gross_scaled():
    score = _score()
    pc = PortfolioConstructor(ConstructionConfig(gross=1.0, max_position=1.0,
                                                 smoothing_halflife=None, target_vol=None))
    w = pc.build(score, score * 0)
    row = w.iloc[-1]
    assert abs(row.sum()) < 1e-9                       # dollar-neutral (net ≈ 0)
    assert row.abs().sum() == pytest.approx(1.0)       # gross == 1


def test_position_cap_binds():
    score = _score()
    score.iloc[:, 0] = 50                              # one name dominates
    pc = PortfolioConstructor(ConstructionConfig(gross=1.0, max_position=0.10,
                                                 smoothing_halflife=None, target_vol=None))
    w = pc.build(score, score * 0)
    assert w.iloc[-1].abs().max() <= 0.10 + 1e-9       # capped at 10% of gross


def test_smoothing_reduces_turnover():
    score = _score(seed=1)
    rets = score * 0
    raw = PortfolioConstructor(ConstructionConfig(smoothing_halflife=None, target_vol=None,
                                                  max_position=1.0)).build(score, rets)
    smooth = PortfolioConstructor(ConstructionConfig(smoothing_halflife=20, target_vol=None,
                                                     max_position=1.0)).build(score, rets)
    raw_to = raw.diff().abs().sum(axis=1).mean()
    smooth_to = smooth.diff().abs().sum(axis=1).mean()
    assert smooth_to < raw_to * 0.6                    # materially lower churn


def test_vol_targeting_hits_target():
    # random scores + random asset returns → book scaled to ~10% annual vol
    score = _score(n=800, seed=2)
    rets = pd.DataFrame(np.random.default_rng(3).normal(0, 0.02, score.shape),
                        index=score.index, columns=score.columns)
    pc = PortfolioConstructor(ConstructionConfig(target_vol=0.10, vol_lookback=60,
                                                 smoothing_halflife=None, max_position=1.0))
    w = pc.build(score, rets)
    port = (w.shift(1) * rets).sum(axis=1).iloc[200:]   # after warmup
    realized = port.std(ddof=0) * np.sqrt(252)
    assert 0.05 < realized < 0.20                       # in the right ballpark of 10%
