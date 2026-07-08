"""Alpha selection: VIF pruning drops collinear signals; IC weights are normalized."""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.factors.selection import ic_weights, vif_prune


def _panels():
    idx = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    cols = ["X", "Y", "Z"]
    rng = np.random.default_rng(0)
    a = pd.DataFrame(rng.normal(size=(200, 3)), index=idx, columns=cols)
    c = pd.DataFrame(rng.normal(size=(200, 3)), index=idx, columns=cols)
    return {
        "alpha_a": a,
        "alpha_a_dup": a + 0.001 * rng.normal(size=(200, 3)),   # nearly collinear with a
        "alpha_c": c,                                            # independent
    }


def test_vif_prune_drops_one_of_the_collinear_pair():
    kept = vif_prune(_panels(), threshold=5.0)
    assert "alpha_c" in kept                                    # independent always survives
    assert ("alpha_a" in kept) ^ ("alpha_a_dup" in kept)       # exactly one of the dup pair
    assert len(kept) == 2


def test_ic_weights_normalized():
    sig = _panels()
    prices = pd.DataFrame(
        100 + np.random.default_rng(1).normal(size=(200, 3)).cumsum(0),
        index=sig["alpha_a"].index, columns=["X", "Y", "Z"],
    )
    w = ic_weights(sig, prices, horizon=5)
    assert set(w) == set(sig)
    assert sum(abs(v) for v in w.values()) == __import__("pytest").approx(1.0)
