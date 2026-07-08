"""Covariance estimation for mean-variance / Black-Litterman.

The sample covariance is noisy and often ill-conditioned (singular when T < N), which makes
naive Markowitz blow up — the deep-research synthesis flagged this directly. Ledoit-Wolf
shrinkage pulls the sample estimate toward a structured target (here scaled identity),
trading a little bias for much lower variance and a well-conditioned, invertible matrix.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sample_cov(returns: pd.DataFrame) -> np.ndarray:
    return np.cov(returns.dropna(how="any").to_numpy(), rowvar=False)


def ledoit_wolf(returns: pd.DataFrame) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf (2004) shrinkage toward a scaled identity. Returns (Sigma, shrinkage)."""
    x = returns.dropna(how="any").to_numpy()
    t, n = x.shape
    xc = x - x.mean(axis=0)
    s = (xc.T @ xc) / t                       # MLE sample covariance
    mu = np.trace(s) / n                       # average variance → identity target F = mu*I
    f = mu * np.eye(n)

    d2 = np.sum((s - f) ** 2) / n              # dispersion of S around target
    # b̄² : average squared error of the per-observation covariance estimates (vectorized)
    norm4 = np.einsum("ij,ij->i", xc, xc) ** 2          # ||x_t||^4
    xsx = np.einsum("ij,jk,ik->i", xc, s, xc)           # x_t' S x_t
    b2 = float((norm4 - 2 * xsx + np.sum(s ** 2)).sum() / t ** 2 / n)
    b2 = min(b2, d2)                           # bound (LW): can't shrink more than the dispersion
    shrink = 0.0 if d2 == 0 else b2 / d2
    sigma = shrink * f + (1 - shrink) * s
    return sigma, float(shrink)
