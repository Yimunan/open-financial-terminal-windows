"""Black-Litterman model — blend a market-equilibrium prior with investor views to get a
posterior expected-return vector that's far more stable than raw sample means (MPT's weak
point).

Steps:
  1. **Implied equilibrium returns** π = δ Σ w_market — reverse-optimize the returns the
     market-cap weights would be optimal for.
  2. **Posterior** via the He-Litterman formula, combining π (prior) with views (P, Q, Ω):
       μ_BL = [(τΣ)⁻¹ + Pᵀ Ω⁻¹ P]⁻¹ [(τΣ)⁻¹ π + Pᵀ Ω⁻¹ Q]
  3. Feed μ_BL into ``optimize.max_sharpe`` / ``mean_variance``.

For an alpha-driven book, the alpha score becomes **absolute views** (P = I, Q ∝ score), with
Ω encoding per-view confidence. Higher confidence (smaller Ω) pulls the posterior toward the
alpha; lower confidence leaves it near equilibrium.
"""

from __future__ import annotations

import numpy as np


def implied_returns(cov: np.ndarray, market_weights: np.ndarray, risk_aversion: float = 2.5) -> np.ndarray:
    """π = δ Σ w_market — the equilibrium (prior) expected returns."""
    return risk_aversion * cov @ np.asarray(market_weights, float)


def black_litterman(cov: np.ndarray, pi: np.ndarray, p: np.ndarray, q: np.ndarray,
                    omega: np.ndarray | None = None, tau: float = 0.05) -> np.ndarray:
    """Posterior expected returns. ``p`` (K×N), ``q`` (K,), ``omega`` (K×K, default
    diag(P τΣ Pᵀ) — confidence proportional to view variance)."""
    tau_sigma = tau * cov
    if omega is None:
        omega = np.diag(np.diag(p @ tau_sigma @ p.T))
    inv_ts = np.linalg.inv(tau_sigma)
    inv_om = np.linalg.inv(omega)
    a = inv_ts + p.T @ inv_om @ p
    b = inv_ts @ np.asarray(pi, float) + p.T @ inv_om @ np.asarray(q, float)
    return np.linalg.solve(a, b)


def absolute_views(scores: np.ndarray, confidence: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Turn a per-asset score vector into absolute views: P = I, Q = scores, Ω = (1/conf)·I.
    Larger ``confidence`` → smaller Ω → posterior leans toward the scores."""
    n = len(scores)
    p = np.eye(n)
    q = np.asarray(scores, float)
    omega = np.eye(n) / max(confidence, 1e-12)
    return p, q, omega
