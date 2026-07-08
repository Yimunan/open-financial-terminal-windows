"""Deflated Sharpe Ratio: inverse-normal accuracy, PSR behavior, and that deflation makes
the bar rise with the number of trials searched."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.evaluation.deflated_sharpe import (
    _norm_ppf,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)


def test_inverse_normal_accuracy():
    assert _norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-4)
    assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)


def _returns(mean, n=1000, seed=0):
    return pd.Series(np.random.default_rng(seed).normal(mean, 0.01, n))


def test_psr_high_for_strong_positive_sharpe():
    assert probabilistic_sharpe_ratio(_returns(0.001)) > 0.9
    flat = _returns(0.0)
    flat = flat - flat.mean()                       # exactly zero realized Sharpe → PSR = 0.5
    assert probabilistic_sharpe_ratio(flat) == pytest.approx(0.5, abs=1e-6)


def test_expected_max_sharpe_grows_with_trials():
    a = expected_max_sharpe(2, 0.01)
    b = expected_max_sharpe(100, 0.01)
    assert 0 < a < b
    assert expected_max_sharpe(50, 0.0) == 0.0      # no dispersion → no inflation


def test_deflation_raises_the_bar():
    r = _returns(0.0005, seed=3)
    psr0 = probabilistic_sharpe_ratio(r, 0.0)
    dsr_few = deflated_sharpe_ratio(r, n_trials=2, sr_variance=0.01)
    dsr_many = deflated_sharpe_ratio(r, n_trials=200, sr_variance=0.01)
    assert dsr_few <= psr0 + 1e-9                    # deflating vs SR0>=0 can only lower it
    assert dsr_many < dsr_few                        # wider search → harder to clear
