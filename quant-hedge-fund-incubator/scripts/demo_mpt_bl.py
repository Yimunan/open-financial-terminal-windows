"""Demonstrate MPT (mean-variance) and Black-Litterman on the real pool's latest cross-section:
shrinkage covariance from trailing returns, the momentum alpha as the expected-return view,
and the resulting optimal weights — plus how Black-Litterman blends a market-equilibrium prior
with that view.

  .venv\\Scripts\\python.exe scripts\\demo_mpt_bl.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.factors import transforms as tf
from qhfi.factors.library import MomentumFactor
from qhfi.portfolio import black_litterman as bl
from qhfi.portfolio import optimize as mv
from qhfi.portfolio.covariance import ledoit_wolf

LOOKBACK = 252


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    store = market_store()
    prices = store.load_panel(universe.instruments, "close").dropna(axis=1, how="any")
    cols = list(prices.columns)
    rets = prices.pct_change().iloc[-LOOKBACK:]
    print(f"Pool: {universe.name} | {len(cols)} names | trailing {LOOKBACK}d "
          f"to {prices.index.max().date()}\n")

    # covariance (shrunk) + expected-return view (sector-neutral momentum z-score, latest row)
    sigma, shrink = ledoit_wolf(rets)
    mom = MomentumFactor().compute(prices, universe)
    sectors = universe.groups("gics_sector")
    mu = tf.neutralize(tf.zscore(mom), sectors).iloc[-1].reindex(cols).fillna(0.0).to_numpy()
    mu = mu * 0.001                                     # scale z-score to a daily-return view
    print(f"Ledoit-Wolf shrinkage intensity: {shrink:.2f}  (0=sample cov, 1=identity)\n")

    # ── MPT: max-Sharpe long-short, dollar-neutral, gross 1 ──
    w_mpt = pd.Series(mv.max_sharpe(mu, sigma, gross=1.0, dollar_neutral=True), index=cols)
    print("MPT max-Sharpe (long-short, dollar-neutral) — top longs / shorts:")
    print("  long :", w_mpt.sort_values().tail(4).round(3).to_dict())
    print("  short:", w_mpt.sort_values().head(4).round(3).to_dict())
    print(f"  gross={w_mpt.abs().sum():.2f}  net={w_mpt.sum():+.3f}\n")

    # ── Black-Litterman: equilibrium prior + momentum as absolute views ──
    w_mkt = np.full(len(cols), 1 / len(cols))           # equal-weight proxy for market caps
    pi = bl.implied_returns(sigma, w_mkt, risk_aversion=2.5)
    # scale the alpha view to equilibrium magnitude (BL is sensitive to relative scaling),
    # and let Ω default to diag(P·τΣ·Pᵀ) so confidence is calibrated to the covariance.
    mu_view = mu / (np.linalg.norm(mu) or 1.0) * np.linalg.norm(pi)
    p = np.eye(len(cols))
    mu_bl = bl.black_litterman(sigma, pi, p, mu_view, omega=None, tau=0.05)
    blend = np.corrcoef(mu_bl, pi)[0, 1], np.corrcoef(mu_bl, mu_view)[0, 1]
    print("Black-Litterman posterior vs prior(π) and view(α):")
    print(f"  corr(μ_BL, π_equilibrium) = {blend[0]:+.2f}   corr(μ_BL, α_view) = {blend[1]:+.2f}")
    w_bl = pd.Series(mv.max_sharpe(mu_bl, sigma, gross=1.0, dollar_neutral=True), index=cols)
    print("  BL max-Sharpe — top longs:", w_bl.sort_values().tail(4).round(3).to_dict())
    print(f"  gross={w_bl.abs().sum():.2f}  net={w_bl.sum():+.3f}")

    print("\nNote: equal-weight is a stand-in for market-cap equilibrium (EquityMeta.market_cap")
    print("is unpopulated). Naive MV on noisy α is fragile — shrinkage + BL are the stabilizers.")


if __name__ == "__main__":
    main()
