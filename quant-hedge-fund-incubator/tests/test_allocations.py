"""Factor-free long-only allocators: valid weights, and the structural properties
(inverse-vol tilts to low-vol names; min-variance has lower in-sample variance than 1/N)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.portfolio import allocations as alloc
from qhfi.portfolio.covariance import ledoit_wolf


def _returns(seed=0):
    rng = np.random.default_rng(seed)
    # 4 assets with increasing volatility
    vols = np.array([0.005, 0.01, 0.02, 0.04])
    data = rng.normal(0, 1, (500, 4)) * vols
    return pd.DataFrame(data, columns=["A", "B", "C", "D"])


@pytest.mark.parametrize("fn", list(alloc.ALLOCATORS.values()))
def test_weights_are_valid_long_only(fn):
    w = fn(_returns())
    assert np.all(w >= -1e-12)                       # long-only
    assert w.sum() == pytest.approx(1.0)             # fully invested


def test_inverse_vol_tilts_to_low_vol():
    w = alloc.inverse_vol(_returns())
    assert w[0] > w[1] > w[2] > w[3]                 # lowest-vol asset gets the most


def test_min_variance_beats_equal_weight_in_variance():
    r = _returns(1)
    sigma, _ = ledoit_wolf(r)
    w_mv = alloc.min_variance_long_only(r)
    w_eq = alloc.equal_weight(r)
    assert w_mv @ sigma @ w_mv <= w_eq @ sigma @ w_eq   # GMV ≤ 1/N variance (in-sample)
