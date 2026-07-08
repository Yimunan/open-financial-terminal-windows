"""Combine the Alpha101 starter set into a validated long-short strategy, following the
research pipeline: VIF-prune → standardize + sector-neutralize → combine (equal & IC-IR) →
granular backtest + walk-forward → Deflated Sharpe (multiple-testing control).

  .venv\\Scripts\\python.exe scripts\\build_multialpha.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.financing import FinancingModel
from qhfi.backtest.fills import SlippageModel
from qhfi.backtest.validation import WalkForwardConfig, concat_oos, walk_forward
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.evaluation import metrics
from qhfi.evaluation.deflated_sharpe import deflated_sharpe_ratio, per_period_sharpe
from qhfi.factors import transforms as tf
from qhfi.factors.alpha101 import ALL_ALPHAS
from qhfi.factors.market import MarketPanels
from qhfi.factors.selection import ic_weights, vif_prune
from qhfi.strategy.library.factor_strategy import FactorStrategy, FactorStrategyParams

WINDOW = 2520   # ~10y evaluation grid (alphas still computed on full history)


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    panels = MarketPanels.from_store(market_store(), universe)
    prices = panels.close.iloc[-WINDOW:]
    sectors = universe.groups("gics_sector")
    alphas = [cls(panels) for cls in ALL_ALPHAS]
    print(f"Pool: {universe.name} | {prices.shape[1]} names | "
          f"{prices.index.min().date()} → {prices.index.max().date()} ({len(prices)}d)\n")

    # 1. standardized + sector-neutral signals → VIF prune
    signals = {a.name: tf.neutralize(tf.zscore(a.compute(prices, universe)), sectors) for a in alphas}
    kept = vif_prune(signals, threshold=5.0)
    dropped = [a.name for a in alphas if a.name not in kept]
    print(f"VIF prune (threshold 5): kept {len(kept)} {kept}")
    print(f"                         dropped {dropped or 'none'}")

    import numpy as np
    kept_alphas = [a for a in alphas if a.name in kept]
    ic_blend = ic_weights({n: signals[n] for n in kept}, prices, horizon=5)

    eng_net = BacktestEngine()                                          # 10bps fee + 5bps slip + financing
    eng_gross = BacktestEngine(cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
                               financing=FinancingModel(0.0, 0.0, 0.0))  # costless

    # trial Sharpes (the search breadth, for deflation) — gross, per-period
    trial_returns = [
        eng_gross.run(FactorStrategy([a], sectors=sectors,
                                     params=FactorStrategyParams(quantile=0.2)).generate_weights(prices, universe),
                      prices, universe).returns
        for a in alphas
    ]
    sr_var = float(np.var([per_period_sharpe(t) for t in trial_returns], ddof=1))

    # combined strategies: gross vs net + turnover, equal-weight vs IC-IR-weight
    print(f"\n{'scheme':<8} {'grossShrp':>10} {'netShrp':>8} {'turnover':>9} {'netCAGR':>8} {'OOS_net':>8} {'DSR_gross':>10}")
    cfg = WalkForwardConfig(train_days=756, test_days=252, step_days=252, purge_days=10)
    for scheme, blend in [("equal", None), ("IC-IR", ic_blend)]:
        strat = FactorStrategy(kept_alphas, blend=blend, sectors=sectors,
                               params=FactorStrategyParams(quantile=0.2, gross=1.0))
        w = strat.generate_weights(prices, universe)
        gross = eng_gross.run(w, prices, universe)
        net = eng_net.run(w, prices, universe)
        ann_turn = float(net.turnover.mean() * 252)
        gs = metrics.sharpe(gross.returns, periods_per_year=252)
        ns = metrics.sharpe(net.returns, periods_per_year=252)
        oos = concat_oos(walk_forward(strat, prices, universe, eng_net, cfg))
        oos_s = metrics.sharpe(oos, periods_per_year=252) if len(oos) else float("nan")
        dsr = deflated_sharpe_ratio(gross.returns, n_trials=len(alphas), sr_variance=sr_var)
        print(f"{scheme:<8} {gs:>10.2f} {ns:>8.2f} {ann_turn:>8.0f}x {metrics.cagr(net.returns,252):>7.1%} "
              f"{oos_s:>8.2f} {dsr:>10.2%}")

    print("\nDSR = P(true Sharpe > 0) after deflating for the", len(alphas),
          "alphas searched. >97.5% ≈ significant post multiple-testing.")


if __name__ == "__main__":
    main()
