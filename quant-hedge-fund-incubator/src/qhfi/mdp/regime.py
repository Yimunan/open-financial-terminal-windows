"""Market-regime estimation — the discrete Markov *state* of the allocation MDP.

A regime is a cluster of market conditions. We describe each day by two risk features computed
**causally** from trailing returns — rolling annualized volatility and rolling drawdown — and fit
a Gaussian mixture to label every day with a regime. Regimes are then re-indexed by volatility
(``0`` = calmest … ``K-1`` = most volatile) so the labeling is stable across fits and the optimal
policy is monotone-interpretable.

From a label sequence we estimate the empirical **transition matrix** ``P(s'|s)`` (Laplace-
smoothed, the Markov dynamics) and the per-regime return distribution of the risky book (the
inputs to the MDP reward).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_ANN = 252.0


def regime_features(returns: pd.Series, lookback: int = 63) -> pd.DataFrame:
    """Causal risk features per date: trailing annualized vol and trailing drawdown depth."""
    r = returns.astype(float)
    vol = r.rolling(lookback).std(ddof=0) * np.sqrt(_ANN)
    equity = (1.0 + r.fillna(0.0)).cumprod()
    drawdown = equity / equity.rolling(lookback, min_periods=1).max() - 1.0
    return pd.DataFrame({"vol": vol, "drawdown": drawdown})


class RegimeModel:
    """Gaussian-mixture market-regime labeler, vol-ordered for stable regime ids."""

    def __init__(self, n_regimes: int = 3, lookback: int = 63, seed: int = 0) -> None:
        self.n_regimes = n_regimes
        self.lookback = lookback
        self.seed = seed

    def fit(self, market_returns: pd.Series) -> RegimeModel:
        from sklearn.mixture import GaussianMixture

        feats = regime_features(market_returns, self.lookback).dropna(how="any")
        gmm = GaussianMixture(self.n_regimes, covariance_type="full", random_state=self.seed)
        gmm.fit(feats.to_numpy(dtype=float))
        order = np.argsort(gmm.means_[:, 0])               # ascending mean volatility
        self._remap = {int(old): new for new, old in enumerate(order)}
        self.gmm_ = gmm
        self.means_ = gmm.means_[order]                    # vol-ordered cluster centres
        return self

    def label(self, market_returns: pd.Series) -> pd.Series:
        """Regime id per date (vol-ordered), causal; leading rows before ``lookback`` are
        back-filled from the first resolvable regime so weights are always defined."""
        feats = regime_features(market_returns, self.lookback)
        valid = feats.dropna(how="any")
        raw = self.gmm_.predict(valid.to_numpy(dtype=float))
        mapped = np.array([self._remap[int(r)] for r in raw])
        out = pd.Series(np.nan, index=feats.index)
        out.loc[valid.index] = mapped
        return out.ffill().bfill().astype(int)


def transition_matrix(labels: pd.Series, n_regimes: int, smoothing: float = 1.0) -> np.ndarray:
    """Empirical Markov transition matrix ``P[s, s']`` from a label sequence, Laplace-smoothed
    so unobserved transitions stay possible. Rows sum to 1."""
    counts = np.full((n_regimes, n_regimes), smoothing, dtype=float)
    arr = labels.to_numpy(dtype=int)
    for a, b in zip(arr[:-1], arr[1:], strict=False):
        counts[a, b] += 1.0
    return counts / counts.sum(axis=1, keepdims=True)


def regime_return_stats(
    risky_returns: pd.Series, labels: pd.Series, n_regimes: int
) -> tuple[np.ndarray, np.ndarray]:
    """Per-regime daily mean and variance of the risky book. Empty regimes fall back to the
    pooled mean/variance so the reward is always well defined."""
    r = risky_returns.reindex(labels.index)
    g_mean, g_var = float(r.mean()), float(r.var(ddof=0))
    mu = np.full(n_regimes, g_mean)
    var = np.full(n_regimes, g_var if g_var > 0 else 1e-8)
    for s in range(n_regimes):
        sub = r[labels == s].dropna()
        if len(sub) >= 2:
            mu[s] = float(sub.mean())
            var[s] = float(sub.var(ddof=0)) or var[s]
    return mu, var
