"""MDPStrategy — trade the regime-switching allocation policy as a Strategy.

The MDP decides *how much* risk to hold in each market regime; this strategy turns that policy
into ``TargetWeights``: build a long-only risky book over the universe (equal- or inverse-vol
weighted), infer the regime on each date, and scale the whole book by that regime's optimal risky
fraction (the remainder is implicitly cash). The backtest engine applies its one-bar execution lag.

Honest-OOS note: regime *labels* are causal (trailing features only), but the transition matrix and
per-regime reward stats are estimated on the supplied window — the same in-sample caveat as
``ModelStrategy``'s refit mode. Drive it through ``backtest.validation.walk_forward`` (which keeps
only each fold's test slice) for a clean out-of-sample track.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.mdp.allocation import RegimeAllocationMDP
from qhfi.portfolio.covariance import ledoit_wolf
from qhfi.strategy.base import Strategy, StrategyParams
from qhfi.strategy.registry import register


class MDPStrategyParams(StrategyParams):
    n_regimes: int = 3
    lookback: int = 63           # trailing window for regime features
    gamma: float = 0.95          # MDP discount
    risk_aversion: float = 3.0   # CRRA / mean-variance risk aversion
    max_leverage: float = 1.5    # top of the action grid (risky fraction)
    action_step: float = 0.25    # action-grid spacing
    base: str = "equal"          # risky-book weighting: "equal" | "inverse_vol"


@register
class MDPStrategy(Strategy):
    """Regime-switching allocation: scale a risky book by the MDP's optimal per-regime fraction."""

    name = "mdp"
    params_model = MDPStrategyParams

    def __init__(self, params: MDPStrategyParams | None = None) -> None:
        super().__init__(params)
        self.fitted_: RegimeAllocationMDP | None = None

    def _base_weights(self, returns: Panel) -> np.ndarray:
        """Long-only risky-book weights summing to 1 (equal or inverse-vol)."""
        p: MDPStrategyParams = self.params  # type: ignore[assignment]
        n = returns.shape[1]
        if p.base == "inverse_vol":
            sigma, _ = ledoit_wolf(returns)
            inv = 1.0 / np.sqrt(np.clip(np.diag(sigma), 1e-12, None))
            return inv / inv.sum()
        return np.full(n, 1.0 / n)

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        p: MDPStrategyParams = self.params  # type: ignore[assignment]
        returns = prices.pct_change()
        base = self._base_weights(returns.dropna(how="all"))
        book_returns = pd.Series(returns.to_numpy() @ base, index=returns.index).fillna(0.0)

        grid = tuple(np.round(np.arange(0.0, p.max_leverage + 1e-9, p.action_step), 6))
        mdp = RegimeAllocationMDP(
            n_regimes=p.n_regimes, lookback=p.lookback, gamma=p.gamma,
            risk_aversion=p.risk_aversion, action_grid=grid,
        ).fit(book_returns, book_returns)
        self.fitted_ = mdp

        labels = mdp.label(book_returns)
        fraction = labels.map(lambda s: mdp.optimal_fraction(int(s))).to_numpy()  # (T,)
        weights = np.outer(fraction, base)                                        # (T, N)
        return pd.DataFrame(weights, index=prices.index, columns=prices.columns)
