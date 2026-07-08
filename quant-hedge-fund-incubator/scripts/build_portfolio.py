"""Portfolio construction on the combined Alpha101 signal: compare the raw quantile book
against turnover-controlled, vol-targeted, position-capped construction — gross vs net,
with turnover and Deflated Sharpe. Shows whether controlling turnover rescues net Sharpe.

  .venv\\Scripts\\python.exe scripts\\build_portfolio.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.financing import FinancingModel
from qhfi.backtest.fills import SlippageModel
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.evaluation import metrics
from qhfi.evaluation.deflated_sharpe import deflated_sharpe_ratio, per_period_sharpe
from qhfi.factors import transforms as tf
from qhfi.factors.alpha101 import ALL_ALPHAS
from qhfi.factors.market import MarketPanels
from qhfi.factors.selection import vif_prune
from qhfi.portfolio.construction import ConstructionConfig, PortfolioConstructor
from qhfi.strategy.library.factor_strategy import FactorStrategy, FactorStrategyParams

WINDOW = 2520


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    panels = MarketPanels.from_store(market_store(), universe)
    prices = panels.close.iloc[-WINDOW:]
    returns = prices.pct_change()
    sectors = universe.groups("gics_sector")
    alphas = [cls(panels) for cls in ALL_ALPHAS]

    signals = {a.name: tf.neutralize(tf.zscore(a.compute(prices, universe)), sectors) for a in alphas}
    kept = vif_prune(signals, threshold=5.0)
    kept_alphas = [a for a in alphas if a.name in kept]
    print(f"Pool: {universe.name} | {prices.shape[1]} names | {len(prices)}d | kept {len(kept)} alphas\n")

    base = FactorStrategy(kept_alphas, sectors=sectors, params=FactorStrategyParams(quantile=0.2, gross=1.0))
    score = base.composite_score(prices, universe)

    eng_net = BacktestEngine()
    eng_gross = BacktestEngine(cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
                               financing=FinancingModel(0.0, 0.0, 0.0))

    # deflation reference: per-alpha gross Sharpes
    trial_sr = [per_period_sharpe(eng_gross.run(
        FactorStrategy([a], sectors=sectors, params=FactorStrategyParams(quantile=0.2)).generate_weights(prices, universe),
        prices, universe).returns) for a in alphas]
    sr_var = float(np.var(trial_sr, ddof=1))

    books = {
        "quantile L/S (baseline)": base.generate_weights(prices, universe),
        "constructed (no smooth)": PortfolioConstructor(ConstructionConfig(
            max_position=0.05, smoothing_halflife=None, target_vol=0.10)).build(score, returns),
        "constructed (smooth 10)": PortfolioConstructor(ConstructionConfig(
            max_position=0.05, smoothing_halflife=10, target_vol=0.10)).build(score, returns),
        "constructed (smooth 20)": PortfolioConstructor(ConstructionConfig(
            max_position=0.05, smoothing_halflife=20, target_vol=0.10)).build(score, returns),
    }

    print(f"{'book':<26} {'grossShrp':>10} {'netShrp':>8} {'turnover':>9} {'netCAGR':>8} {'DSR_gross':>10}")
    for name, w in books.items():
        gross = eng_gross.run(w, prices, universe)
        net = eng_net.run(w, prices, universe)
        ann_turn = float(net.turnover.mean() * 252)
        gs = metrics.sharpe(gross.returns, periods_per_year=252)
        ns = metrics.sharpe(net.returns, periods_per_year=252)
        dsr = deflated_sharpe_ratio(gross.returns, n_trials=len(alphas), sr_variance=sr_var)
        print(f"{name:<26} {gs:>10.2f} {ns:>8.2f} {ann_turn:>8.0f}x {metrics.cagr(net.returns,252):>7.1%} {dsr:>10.2%}")

    print("\nVol-targeted, dollar-neutral, 5% name cap. Smoothing the score is the turnover lever;")
    print("compare netShrp as turnover falls. DSR deflates gross Sharpe for the 8 alphas searched.")


if __name__ == "__main__":
    main()
