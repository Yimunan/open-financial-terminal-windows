"""Mean-variance (Markowitz / MPT) portfolio optimization — analytic, closed-form solutions
(no QP solver dependency). A small ridge regularizes the covariance solve for stability.

* ``min_variance`` — global minimum-variance, long weights summing to 1.
* ``mean_variance`` — w = (1/δ) Σ⁻¹ μ (utility max at risk-aversion δ).
* ``max_sharpe`` — tangency direction Σ⁻¹ μ, optionally dollar-neutral, scaled to a gross.

Hard per-name caps / long-only-with-bounds would need a QP; for those, clip + renormalize
(`long_only`) or use the heuristic constructor. Naive MV is fragile on noisy alpha μ — pair
with shrinkage covariance (``covariance.ledoit_wolf``).
"""

from __future__ import annotations

import numpy as np


def _solve(cov: np.ndarray, b: np.ndarray, ridge: float = 1e-8) -> np.ndarray:
    n = cov.shape[0]
    return np.linalg.solve(cov + ridge * np.eye(n), b)


def min_variance(cov: np.ndarray, ridge: float = 1e-8) -> np.ndarray:
    w = _solve(cov, np.ones(cov.shape[0]), ridge)
    return w / w.sum()


def mean_variance(mu: np.ndarray, cov: np.ndarray, risk_aversion: float = 1.0,
                  ridge: float = 1e-8) -> np.ndarray:
    return _solve(cov, np.asarray(mu, float), ridge) / risk_aversion


def max_sharpe(mu: np.ndarray, cov: np.ndarray, gross: float = 1.0,
               dollar_neutral: bool = False, long_only: bool = False,
               ridge: float = 1e-8) -> np.ndarray:
    """Tangency direction Σ⁻¹μ, scaled so |w|.sum() == gross."""
    w = _solve(cov, np.asarray(mu, float), ridge)
    if dollar_neutral:
        w = w - w.mean()
    if long_only:
        w = np.clip(w, 0, None)
    s = np.abs(w).sum()
    return w / s * gross if s else w
