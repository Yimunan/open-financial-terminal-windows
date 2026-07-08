"""BarraMinVarStrategy — a minimum-variance book built on the Barra risk model.

The most direct way to *use* (and stress-test) a risk model: at each monthly rebalance, fit the
Barra model on a trailing window, form its asset covariance ``Σ = X F Xᵀ + diag(Δ)``, and hold the
long-only **minimum-variance** portfolio ``w ∝ Σ⁻¹1`` until the next rebalance. If the structured
Σ forecasts risk better than a raw sample covariance, this book realizes lower volatility and
drawdown — the practical payoff of the factor model.

Like :class:`~qhfi.strategy.library.factor_strategy.FactorStrategy` it carries its inputs (the full
``MarketPanels`` — the Strategy interface only passes close prices, but Barra needs volume for the
ADV cap proxy), so it is constructed explicitly rather than pulled from the string registry.

Causality: exposures and each rebalance fit use only data through the rebalance date; weights are
held forward and the engine adds the one-bar execution lag. (Refitting per rebalance makes a single
full-history run honest — no need to walk-forward, though it composes with ``walk_forward`` too.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.barra.exposures import cap_proxy, industry_dummies, style_exposures
from qhfi.barra.model import BarraRiskModel
from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.factors.market import MarketPanels
from qhfi.portfolio.optimize import min_variance
from qhfi.strategy.base import Strategy, StrategyParams


class BarraMinVarParams(StrategyParams):
    estimation_window: int = 504   # trailing days per Barra fit (~2y)
    rebalance_days: int = 21       # refit/rebalance cadence (~monthly)
    factor_halflife: int = 252
    specific_halflife: int = 126
    gross: float = 1.0             # long-only book gross exposure


def _min_var_long_only(sigma: pd.DataFrame) -> pd.Series:
    """Long-only minimum-variance weights from a covariance matrix (clip shorts, renormalize)."""
    w = min_variance(sigma.to_numpy())
    w = np.clip(w, 0.0, None)
    total = w.sum()
    w = w / total if total > 0 else np.full(len(w), 1.0 / len(w))
    return pd.Series(w, index=sigma.index)


class BarraMinVarStrategy(Strategy):
    """Construct with the universe's ``MarketPanels``; trades the Barra min-variance book."""

    name = "barra_minvar"
    params_model = BarraMinVarParams

    def __init__(self, panels: MarketPanels, params: BarraMinVarParams | None = None) -> None:
        super().__init__(params)
        self.panels = panels

    def _sliced(self, prices: Panel) -> MarketPanels:
        """Stored panels restricted to the price panel's date/instrument grid (causal-safe)."""
        def sl(panel: Panel) -> Panel:
            return panel.reindex(index=prices.index, columns=prices.columns)

        m = self.panels
        return MarketPanels(sl(m.open), sl(m.high), sl(m.low), sl(m.close), sl(m.volume))

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        p: BarraMinVarParams = self.params  # type: ignore[assignment]
        panels = self._sliced(prices)
        returns = panels.returns
        exposures = style_exposures(panels)
        industries = industry_dummies(universe)
        cap = cap_proxy(panels)

        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        held: pd.Series | None = None
        win = p.estimation_window
        for i in range(len(prices.index)):
            if i >= win and (i - win) % p.rebalance_days == 0:
                sl = slice(i - win, i + 1)
                try:
                    model = BarraRiskModel(p.factor_halflife, p.specific_halflife).fit(
                        returns.iloc[sl],
                        {s: exposures[s].iloc[sl] for s in exposures},
                        industries,
                        cap.iloc[sl],
                    )
                    held = _min_var_long_only(model.covariance()) * p.gross
                except (ValueError, np.linalg.LinAlgError):
                    pass  # not enough data yet — keep prior weights (flat until first fit)
            if held is not None:
                weights.iloc[i] = held.reindex(prices.columns).fillna(0.0).to_numpy()
        return weights
