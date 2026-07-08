"""Solve regime-switching dynamic allocation as a Markov Decision Process, then backtest the
policy against always-invested and constant-fraction baselines and version it in the repository.

Shows the full MDP: market regimes (the state), the regime transition matrix (Markov dynamics),
and the optimal risky fraction + value per regime (Bellman solution) — the policy should de-risk
as volatility rises. Saves the fitted policy under  models/allocation/equity/<name>/.

  .venv\\Scripts\\python.exe scripts\\build_mdp_allocator.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from qhfi.backtest.engine import BacktestEngine
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.evaluation import metrics
from qhfi.factors.market import MarketPanels
from qhfi.models import ModelDomain, ModelRepository, ModelStage
from qhfi.core.types import AssetClass
from qhfi.strategy.library.mdp_strategy import MDPStrategy, MDPStrategyParams

WINDOW = 2520    # ~10y


def _const_weights(base: np.ndarray, fraction: float, like: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(np.tile(base * fraction, (len(like), 1)), index=like.index, columns=like.columns)


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    prices = MarketPanels.from_store(market_store(), universe).close.iloc[-WINDOW:]
    print(f"Pool: {universe.name} | {prices.shape[1]} names | "
          f"{prices.index.min().date()} → {prices.index.max().date()} ({len(prices)}d)\n")

    strat = MDPStrategy(MDPStrategyParams(n_regimes=3, risk_aversion=3.0, max_leverage=1.5))
    weights = strat.generate_weights(prices, universe)
    mdp = strat.fitted_
    assert mdp is not None

    print("Regime transition matrix P(s'|s):")
    print(pd.DataFrame(mdp.P_, index=[f"s{i}" for i in range(mdp.n_regimes)],
                       columns=[f"s{i}" for i in range(mdp.n_regimes)]).round(3).to_string())
    print("\nMDP solution (optimal risky fraction per regime):")
    print(mdp.policy_table().round(3).to_string())

    # Baselines on the same risky book.
    base = strat._base_weights(prices.pct_change().dropna(how="all"))
    eng = BacktestEngine()
    runs = {
        "mdp": weights,
        "always-invested": _const_weights(base, 1.0, weights),
        "constant-60%": _const_weights(base, 0.6, weights),
    }
    print(f"\n{'strategy':<16} {'CAGR':>7} {'vol':>7} {'Sharpe':>7} {'Calmar':>7} {'maxDD':>7}")
    for label, w in runs.items():
        r = eng.run(w, prices, universe).returns
        print(f"{label:<16} {metrics.cagr(r):>6.1%} {metrics.ann_vol(r):>6.1%} "
              f"{metrics.sharpe(r):>7.2f} {metrics.calmar(r):>7.2f} {metrics.max_drawdown(r):>6.1%}")

    # Version the fitted policy under the allocation taxonomy.
    repo = ModelRepository()
    card = repo.save("regime-allocator", mdp, framework="custom",
                     domain=ModelDomain.ALLOCATION, asset_class=AssetClass.EQUITY,
                     params={"n_regimes": 3, "risk_aversion": 3.0, "gamma": mdp.gamma},
                     metrics={f"frac_s{i}": float(mdp.policy_[i]) for i in range(mdp.n_regimes)},
                     train_span=(str(prices.index.min().date()), str(prices.index.max().date())),
                     lineage={"universe": universe.name, "base": "equal"})
    repo.promote(card.name, card.version, ModelStage.PRODUCTION)
    print(f"\nSaved + promoted models/{card.domain.value}/{card.asset_class.value}/{card.name}/v{card.version}/.")
    print("\nNote: the MDP optimizes a one-period CRRA/mean-variance utility — its edge is drawdown/vol "
          "control (lower maxDD, higher Calmar), not raw Sharpe. Params are in-sample here; use "
          "walk_forward for an honest OOS track.")


if __name__ == "__main__":
    main()
