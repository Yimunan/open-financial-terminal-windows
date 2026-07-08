"""Mean-variance (MPT), Ledoit-Wolf shrinkage, and Black-Litterman — closed-form properties."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.portfolio import black_litterman as bl
from qhfi.portfolio import optimize as mv
from qhfi.portfolio.covariance import ledoit_wolf


# ── mean-variance ────────────────────────────────────────────────────────────
def test_min_variance_favors_low_variance_asset():
    cov = np.diag([1.0, 4.0])
    w = mv.min_variance(cov)
    assert w.sum() == pytest.approx(1.0)
    assert w[0] == pytest.approx(0.8) and w[1] == pytest.approx(0.2)   # 1/var weighting


def test_max_sharpe_is_sigma_inv_mu_dollar_neutral():
    cov = np.eye(3)
    w = mv.max_sharpe(np.array([1.0, 2.0, 3.0]), cov, gross=1.0, dollar_neutral=True)
    # Σ=I → w ∝ μ - mean(μ) = [-1,0,1] → scaled to gross 1 → [-0.5,0,0.5]
    assert np.allclose(w, [-0.5, 0.0, 0.5])
    assert abs(w.sum()) < 1e-12 and np.abs(w).sum() == pytest.approx(1.0)


# ── shrinkage ────────────────────────────────────────────────────────────────
def test_ledoit_wolf_is_valid_and_bounded():
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0, 0.01, (250, 20)))
    sigma, shrink = ledoit_wolf(rets)
    assert 0.0 <= shrink <= 1.0
    assert np.allclose(sigma, sigma.T)                                  # symmetric
    assert np.all(np.linalg.eigvalsh(sigma) > 0)                        # PSD / invertible


# ── Black-Litterman ──────────────────────────────────────────────────────────
def test_implied_returns():
    cov = np.eye(2)
    assert np.allclose(bl.implied_returns(cov, [0.5, 0.5], risk_aversion=2.0), [1.0, 1.0])


def test_posterior_blends_prior_and_views():
    cov = np.eye(3)
    pi = np.zeros(3)                                   # prior: zero
    q = np.array([1.0, 1.0, 1.0])                      # view: +1 each
    p, _, _ = bl.absolute_views(q)

    # default Ω = τΣ → posterior is the midpoint between prior(0) and view(1)
    mid = bl.black_litterman(cov, pi, p, q, omega=None, tau=0.05)
    assert np.allclose(mid, 0.5, atol=1e-9)

    # high confidence (tiny Ω) → posterior ≈ views
    _, _, om_hi = bl.absolute_views(q, confidence=1e6)
    near_view = bl.black_litterman(cov, pi, p, q, omega=om_hi, tau=0.05)
    assert np.allclose(near_view, 1.0, atol=1e-2)

    # low confidence (huge Ω) → posterior ≈ prior
    _, _, om_lo = bl.absolute_views(q, confidence=1e-6)
    near_prior = bl.black_litterman(cov, pi, p, q, omega=om_lo, tau=0.05)
    assert np.allclose(near_prior, 0.0, atol=1e-2)
