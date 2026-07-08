"""Regime-switching dynamic allocation as a Markov Decision Process.

Ties the pieces together: estimate market regimes (the state) and their transition matrix, build
the reward of each (regime, allocation) pair from the per-regime risky-return distribution, and
solve the Bellman equation for the optimal risky fraction in each regime.

* **State**      — market regime ``s`` (from :class:`~qhfi.mdp.regime.RegimeModel`).
* **Action**     — risky-book fraction ``a`` on a grid (rest in cash); ``a>1`` is leverage.
* **Reward**     — one-period mean-variance utility (a CRRA approximation):
  ``R(s,a) = a·μ_s + (1-a)·r_f − ½·γ_risk·a²·σ²_s``.
* **Transition** — empirical regime Markov matrix (exogenous; the action sets exposure, not the
  market).
* **Solve**      — :func:`~qhfi.mdp.core.value_iteration` → optimal fraction per regime.

The fitted object is small and picklable, so it versions cleanly in the ``ModelRepository`` under
``ModelDomain.ALLOCATION``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.mdp.core import MDP, value_iteration
from qhfi.mdp.regime import RegimeModel, regime_return_stats, transition_matrix

DEFAULT_ACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)


class RegimeAllocationMDP:
    """Fit a regime-switching allocation policy by solving a finite MDP."""

    def __init__(
        self,
        n_regimes: int = 3,
        lookback: int = 63,
        gamma: float = 0.95,
        risk_aversion: float = 3.0,
        rf_annual: float = 0.0,
        action_grid: tuple[float, ...] = DEFAULT_ACTIONS,
        seed: int = 0,
    ) -> None:
        self.n_regimes = n_regimes
        self.lookback = lookback
        self.gamma = gamma
        self.risk_aversion = risk_aversion
        self.rf_annual = rf_annual
        self.action_grid = np.asarray(action_grid, dtype=float)
        self.seed = seed

    def _reward(self, mu: np.ndarray, var: np.ndarray) -> np.ndarray:
        """``R[s,a] = a·μ_s + (1-a)·r_f − ½·γ_risk·a²·σ²_s`` over the action grid."""
        a = self.action_grid[None, :]                       # (1, A)
        rf = self.rf_annual / 252.0
        mean = a * mu[:, None] + (1.0 - a) * rf
        penalty = 0.5 * self.risk_aversion * a**2 * var[:, None]
        return mean - penalty                               # (S, A)

    def fit(self, market_returns: pd.Series, risky_returns: pd.Series) -> RegimeAllocationMDP:
        self.regime_ = RegimeModel(self.n_regimes, self.lookback, self.seed).fit(market_returns)
        labels = self.regime_.label(market_returns)
        self.P_ = transition_matrix(labels, self.n_regimes)
        self.mu_, self.var_ = regime_return_stats(risky_returns, labels, self.n_regimes)
        reward = self._reward(self.mu_, self.var_)
        mdp = MDP(self.P_, reward, self.action_grid, self.gamma)
        self.values_, policy_idx = value_iteration(mdp)
        self.policy_ = self.action_grid[policy_idx]          # regime → optimal risky fraction
        return self

    def optimal_fraction(self, regime: int) -> float:
        """Optimal risky fraction for a regime id."""
        return float(self.policy_[regime])

    def label(self, market_returns: pd.Series) -> pd.Series:
        return self.regime_.label(market_returns)

    def policy_table(self) -> pd.DataFrame:
        """Per-regime summary: ann. mean/vol of the risky book, value, and optimal fraction."""
        return pd.DataFrame(
            {
                "ann_mean": self.mu_ * 252.0,
                "ann_vol": np.sqrt(self.var_ * 252.0),
                "value": self.values_,
                "risky_fraction": self.policy_,
            },
            index=pd.Index(range(self.n_regimes), name="regime"),
        )
