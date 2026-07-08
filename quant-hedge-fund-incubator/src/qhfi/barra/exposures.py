"""Barra-style factor exposures — the design matrix ``X`` of the cross-sectional risk model.

Each style factor is a cross-sectionally **standardized** (winsorize → z-score) characteristic,
so every column is mean≈0 / std≈1 on each date and the factor *returns* (estimated by regression
in :mod:`qhfi.barra.model`) are comparable. Industry membership enters as 0/1 dummies (one column
per GICS sector) — together they span the market intercept, so the model needs no separate one.

All exposures are derived from price + volume only, so they have **full coverage** on every name
(unlike the sparse fundamentals on disk). Because the lake has no ``market_cap``, dollar **ADV**
``(close·volume)`` is the size / cap proxy used both for the Size factor and the regression's WLS
weights — swap in real cap once it's populated. Everything is causal: an exposure at date *t* uses
only data through *t*.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import Panel, Universe
from qhfi.factors import transforms
from qhfi.factors.market import MarketPanels

STYLE_FACTORS = ("size", "beta", "momentum", "resid_vol", "reversal")


def _standardize(panel: Panel, winsor: float = 0.02) -> Panel:
    """Cross-sectional winsorize then z-score — one standardized exposure column per date."""
    return transforms.zscore(transforms.winsorize(panel, winsor, 1 - winsor))


def _rolling_beta(returns: Panel, market: pd.Series, window: int) -> tuple[Panel, Panel]:
    """Rolling CAPM beta of each name on the market, plus residual returns r − β·r_m."""
    rim = returns.mul(market, axis=0)
    cov = rim.rolling(window).mean().sub(
        returns.rolling(window).mean().mul(market.rolling(window).mean(), axis=0)
    )
    var_m = market.rolling(window).var(ddof=0)
    beta = cov.div(var_m, axis=0)
    resid = returns.sub(beta.mul(market, axis=0))
    return beta, resid


def cap_proxy(panels: MarketPanels, window: int = 20) -> Panel:
    """Dollar ADV ``(close·volume)`` rolling mean — the market-cap stand-in (lake has no cap)."""
    return panels.adv(window)


def style_exposures(
    panels: MarketPanels,
    market: pd.Series | None = None,
    beta_window: int = 252,
    vol_window: int = 63,
    mom_lookback: int = 252,
    mom_gap: int = 21,
    rev_window: int = 21,
    adv_window: int = 20,
    winsor: float = 0.02,
) -> dict[str, Panel]:
    """Standardized style-exposure panels keyed by name (each dates × instrument).

    * **size**      — log dollar ADV (cap proxy).
    * **beta**      — rolling CAPM beta vs the equal-weight market.
    * **momentum**  — 12-1 total return (skip the most recent month).
    * **resid_vol** — volatility of market-residual returns (the idiosyncratic-risk style).
    * **reversal**  — trailing one-month return (short-term reversal).
    """
    returns = panels.returns
    mkt = returns.mean(axis=1) if market is None else market
    beta, resid = _rolling_beta(returns, mkt, beta_window)

    raw = {
        "size": np.log(cap_proxy(panels, adv_window).replace(0.0, np.nan)),
        "beta": beta,
        "momentum": panels.close.shift(mom_gap) / panels.close.shift(mom_gap + mom_lookback) - 1.0,
        "resid_vol": resid.rolling(vol_window).std(ddof=0),
        "reversal": panels.close.pct_change(rev_window),
    }
    return {name: _standardize(panel, winsor) for name, panel in raw.items()}


def industry_dummies(universe: Universe, level: str = "gics_sector") -> pd.DataFrame:
    """One-hot GICS-sector membership as an ``(instrument × industry)`` frame of 0/1 dummies."""
    groups = universe.groups(level)
    ids = universe.ids
    sectors = sorted({groups[i] for i in ids})
    data = {s: [1.0 if groups[i] == s else 0.0 for i in ids] for s in sectors}
    return pd.DataFrame(data, index=ids)
