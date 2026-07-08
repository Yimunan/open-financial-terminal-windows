"""Build a Barra-style cross-sectional risk model on the equity pool, inspect its factors and
risk decomposition, validate calibration with the bias statistic, and backtest the risk-model
minimum-variance book against equal-weight and a Ledoit-Wolf min-var baseline.

  .venv\\Scripts\\python.exe scripts\\build_barra_model.py

Note: the lake has no market cap, so dollar ADV is the Size/cap proxy and fundamentals (sparse on
disk) are omitted — the factor set is price/volume styles + GICS industry dummies, full coverage.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from qhfi.backtest.engine import BacktestEngine
from qhfi.barra.model import BarraRiskModel, bias_statistic
from qhfi.core.types import AssetClass
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.evaluation import metrics
from qhfi.factors.market import MarketPanels
from qhfi.models import ModelDomain, ModelRepository, ModelStage
from qhfi.portfolio.covariance import ledoit_wolf
from qhfi.portfolio.optimize import min_variance
from qhfi.strategy.library.barra_minvar import BarraMinVarParams, BarraMinVarStrategy

WINDOW = 1260   # ~5y evaluation grid (keeps the rolling backtest snappy)
EST, STEP = 504, 21


def _const(weights: np.ndarray, like: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(np.tile(weights, (len(like), 1)), index=like.index, columns=like.columns)


def _rolling_lw_minvar(returns: pd.DataFrame) -> pd.DataFrame:
    """Rolling long-only min-var on a Ledoit-Wolf covariance — the non-factor baseline."""
    w = pd.DataFrame(0.0, index=returns.index, columns=returns.columns)
    held = None
    for i in range(len(returns)):
        if i >= EST and (i - EST) % STEP == 0:
            sub = returns.iloc[i - EST: i + 1].dropna(axis=0, how="all").dropna(axis=1, how="any")
            if sub.shape[1] < 2:
                continue
            sigma, _ = ledoit_wolf(sub)
            x = np.clip(min_variance(sigma), 0.0, None)
            x = x / x.sum() if x.sum() > 0 else np.full(len(x), 1 / len(x))
            held = pd.Series(x, index=sub.columns).reindex(returns.columns).fillna(0.0)
        if held is not None:
            w.iloc[i] = held.to_numpy()
    return w


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    panels_full = MarketPanels.from_store(market_store(), universe)
    panels = MarketPanels(*(getattr(panels_full, f).iloc[-WINDOW:]
                            for f in ("open", "high", "low", "close", "volume")))
    prices = panels.close
    print(f"Pool: {universe.name} | {prices.shape[1]} names | "
          f"{prices.index.min().date()} → {prices.index.max().date()} ({len(prices)}d)\n")

    # 1. Fit the risk model on the full window and inspect the factors.
    model = BarraRiskModel.from_panels(panels, universe)
    fvol = np.sqrt(np.diag(model.factor_cov(annualize=True).to_numpy()))
    fvol = pd.Series(fvol, index=model.factor_names_)
    styles = list(model.factor_returns_.columns[:5])
    print("Annualized factor volatility (style factors):")
    print("  " + "  ".join(f"{s}={fvol[s]:.1%}" for s in styles))
    print(f"Industries: {len(model.factor_names_) - 5} GICS sectors | "
          f"specific (median) vol={np.sqrt(model.specific_var_.median() * 252):.1%}\n")

    # 2. Risk decomposition of the equal-weight book.
    names = list(model.exposures_.index)
    ew = pd.Series(1.0 / len(names), index=names)
    rd = model.risk_decomposition(ew)
    print(f"Equal-weight book risk: total={rd['total_vol']:.1%}  factor={rd['factor_vol']:.1%}  "
          f"specific={rd['specific_vol']:.1%}  ({rd['pct_factor']:.0%} factor-driven)")
    top = rd["factor_exposures"].reindex(styles).round(2)
    print(f"  style exposures: {dict(top)}\n")

    # 3. Bias statistic — is the forecast calibrated? (rolling std of return/predicted-vol ≈ 1)
    ew_ret = (panels.returns[names] * (1.0 / len(names))).sum(axis=1)
    pred_daily = model.predict_vol(ew) / np.sqrt(252)
    bias = bias_statistic(ew_ret, pd.Series(pred_daily, index=ew_ret.index)).dropna()
    print(f"Bias statistic (equal-weight, 21d): mean={bias.mean():.2f} "
          f"(≈1 is calibrated; >1 under-forecasts risk)\n")

    # 4. Backtest the Barra min-var book vs baselines.
    eng = BacktestEngine()
    barra_w = BarraMinVarStrategy(
        panels, BarraMinVarParams(estimation_window=EST, rebalance_days=STEP)
    ).generate_weights(prices, universe)
    runs = {
        "barra-minvar": barra_w,
        "ledoit-wolf-minvar": _rolling_lw_minvar(panels.returns),
        "equal-weight": _const(np.full(prices.shape[1], 1.0 / prices.shape[1]), prices),
    }
    print(f"{'strategy':<20} {'CAGR':>7} {'annVol':>7} {'Sharpe':>7} {'Calmar':>7} {'maxDD':>7}")
    for label, w in runs.items():
        r = eng.run(w, prices, universe).returns
        print(f"{label:<20} {metrics.cagr(r):>6.1%} {metrics.ann_vol(r):>6.1%} "
              f"{metrics.sharpe(r):>7.2f} {metrics.calmar(r):>7.2f} {metrics.max_drawdown(r):>6.1%}")

    # 5. Version the fitted risk model under the taxonomy.
    repo = ModelRepository()
    card = repo.save("barra-equity", model, framework="custom",
                     domain=ModelDomain.RISK, asset_class=AssetClass.EQUITY,
                     params={"factors": model.factor_names_, "cap_proxy": "dollar_adv20"},
                     metrics={"ew_total_vol": rd["total_vol"], "ew_pct_factor": rd["pct_factor"]},
                     train_span=(str(prices.index.min().date()), str(prices.index.max().date())),
                     lineage={"universe": universe.name})
    repo.promote(card.name, card.version, ModelStage.PRODUCTION)
    print(f"\nSaved + promoted models/{card.domain.value}/{card.asset_class.value}/{card.name}/v{card.version}/.")
    print("\nNote: ADV is the cap proxy (no market cap on disk) and fundamentals are omitted "
          "(sparse) — the model's value is risk forecasting/decomposition; the min-var book should "
          "realize lower vol/drawdown than equal-weight even if Sharpe is similar.")


if __name__ == "__main__":
    main()
