"""Calibrate the two dormant turnover levers — the no-trade band (``rebalance_threshold``)
and score smoothing (``smoothing_halflife``) — on the combined Alpha101 signal.

The research notebook flagged real strategies carrying 20-30x annual turnover with stable
signals, and named the fix ("a no-trade band / signal smoothing ... untested here"). This
sweeps both levers and prints the turnover -> net-Sharpe trade-off so the defaults can be set
at the cost-drag knee (where turnover falls sharply with minimal net-Sharpe loss).

  .venv\\Scripts\\python.exe scripts\\tune_rebalance.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.engine import BacktestEngine, ExecutionConfig
from qhfi.backtest.financing import FinancingModel
from qhfi.backtest.fills import SlippageModel
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.evaluation import metrics
from qhfi.factors import transforms as tf
from qhfi.factors.alpha101 import ALL_ALPHAS
from qhfi.factors.market import MarketPanels
from qhfi.factors.selection import vif_prune
from qhfi.portfolio.construction import ConstructionConfig, PortfolioConstructor
from qhfi.strategy.library.factor_strategy import FactorStrategy, FactorStrategyParams

WINDOW = 2520
# $50M book + fractional sizing — a 100k book on a ~100-name universe fabricates negative
# equity (per-ticket commissions + integer-share rounding dominate). Decompose gross vs net.
EQUITY = 50_000_000.0

# Sweep grid (plan §1).
HALFLIVES: list[int | None] = [None, 5, 10, 20]
BANDS: list[float] = [0.0, 0.0005, 0.001, 0.0025, 0.005]  # fraction of equity per trade


def _net_engine(band: float) -> BacktestEngine:
    """Full-friction engine with the no-trade band under test."""
    return BacktestEngine(
        execution=ExecutionConfig(rebalance_threshold=band, allow_fractional=True),
        initial_equity=EQUITY,
    )


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
    base = FactorStrategy(kept_alphas, sectors=sectors, params=FactorStrategyParams(quantile=0.2, gross=1.0))
    score = base.composite_score(prices, universe)
    print(f"Pool: {universe.name} | {prices.shape[1]} names | {len(prices)}d | kept {len(kept)} alphas\n")

    # Frictionless reference engine — same band=0, isolates the signal's gross Sharpe per book.
    eng_gross = BacktestEngine(
        cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
        financing=FinancingModel(0.0, 0.0, 0.0),
        execution=ExecutionConfig(rebalance_threshold=0.0, allow_fractional=True),
        initial_equity=EQUITY,
    )

    print(f"{'smoothing':<11} {'band':>8} {'grossShrp':>10} {'netShrp':>8} {'turnover':>9} {'netCAGR':>8}")
    for hl in HALFLIVES:
        w = PortfolioConstructor(ConstructionConfig(
            max_position=0.05, smoothing_halflife=hl, target_vol=0.10)).build(score, returns)
        gs = metrics.sharpe(eng_gross.run(w, prices, universe).returns, periods_per_year=252)
        for band in BANDS:
            net = _net_engine(band).run(w, prices, universe)
            ann_turn = float(net.turnover.mean() * 252)
            ns = metrics.sharpe(net.returns, periods_per_year=252)
            cagr = metrics.cagr(net.returns, 252)
            label = "off" if hl is None else f"hl={hl}"
            print(f"{label:<11} {band:>8.4f} {gs:>10.2f} {ns:>8.2f} {ann_turn:>8.0f}x {cagr:>7.1%}")
        print()

    print("Vol-targeted, dollar-neutral, 5% name cap. Pick the (smoothing, band) cell at the knee:")
    print("turnover falls sharply while net Sharpe holds. Those become the settings.yaml defaults.")


if __name__ == "__main__":
    main()
