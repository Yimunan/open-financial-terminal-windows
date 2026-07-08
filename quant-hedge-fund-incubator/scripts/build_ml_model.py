"""Train cross-sectional ML return forecasters on the Alpha101 features, version them in the
ModelRepository, and honestly walk-forward backtest them against the equal-weight factor blend.

Pipeline: VIF-prune the alphas → train Ridge + Gradient-Boosting on the in-sample window
(report in-sample IC) → save to the ModelRepository → in-sample backtest + walk-forward OOS
(refit per fold, embargoed) with net/gross Sharpe, turnover and Deflated Sharpe → promote the
best to PRODUCTION. The equal-weight FactorStrategy is the baseline the models must beat.

  .venv\\Scripts\\python.exe scripts\\build_ml_model.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.costs import BpsCostModel
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
from qhfi.factors.selection import vif_prune
from qhfi.models import ModelRepository, ModelStage
from qhfi.models import train as train_model
from qhfi.models.predictive import ModelSpec
from qhfi.strategy.library.factor_strategy import FactorStrategy, FactorStrategyParams
from qhfi.strategy.library.model_strategy import ModelStrategy, ModelStrategyParams

WINDOW = 2520    # ~10y evaluation grid
HORIZON = 5      # predict 5-day forward returns (matches the multialpha blend horizon)
WF = WalkForwardConfig(train_days=756, test_days=252, step_days=252, purge_days=10)


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    panels = MarketPanels.from_store(market_store(), universe)
    prices = panels.close.iloc[-WINDOW:]
    sectors = universe.groups("gics_sector")
    alphas = [cls(panels) for cls in ALL_ALPHAS]
    print(f"Pool: {universe.name} | {prices.shape[1]} names | "
          f"{prices.index.min().date()} → {prices.index.max().date()} ({len(prices)}d)\n")

    # Feature set: VIF-pruned, sector-neutral standardized alphas (same hygiene the models use).
    signals = {a.name: tf.neutralize(tf.zscore(a.compute(prices, universe)), sectors) for a in alphas}
    kept = vif_prune(signals, threshold=5.0)
    features = [a for a in alphas if a.name in kept]
    print(f"VIF-kept features ({len(kept)}): {kept}\n")

    repo = ModelRepository()
    eng_net = BacktestEngine()                                          # 10bps fee + 5bps slip + financing
    eng_gross = BacktestEngine(cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
                               financing=FinancingModel(0.0, 0.0, 0.0))  # costless

    # Deflation breadth: per-period gross Sharpe of each single-alpha strategy.
    import numpy as np
    trial_returns = [
        eng_gross.run(FactorStrategy([a], sectors=sectors,
                                     params=FactorStrategyParams(quantile=0.2)).generate_weights(prices, universe),
                      prices, universe).returns
        for a in alphas
    ]
    sr_var = float(np.var([per_period_sharpe(t) for t in trial_returns], ddof=1))

    specs = {"ridge": ModelSpec(kind="ridge", params={"alpha": 5.0}),
             "gbr": ModelSpec(kind="gbr", params={"n_estimators": 200, "max_depth": 2,
                                                  "learning_rate": 0.03, "subsample": 0.7})}

    print(f"{'strategy':<12} {'isIC':>7} {'grossShrp':>10} {'netShrp':>8} {'turnover':>9} "
          f"{'netCAGR':>8} {'OOS_net':>8} {'DSR_gross':>10}")

    def report(label: str, strat, oos_strat) -> None:
        w = strat.generate_weights(prices, universe)
        gross = eng_gross.run(w, prices, universe)
        net = eng_net.run(w, prices, universe)
        ann_turn = float(net.turnover.mean() * 252)
        gs = metrics.sharpe(gross.returns, periods_per_year=252)
        ns = metrics.sharpe(net.returns, periods_per_year=252)
        oos = concat_oos(walk_forward(oos_strat, prices, universe, eng_net, WF))
        oos_s = metrics.sharpe(oos, periods_per_year=252) if len(oos) else float("nan")
        dsr = deflated_sharpe_ratio(gross.returns, n_trials=len(alphas), sr_variance=sr_var)
        ic = getattr(strat, "_is_ic", float("nan"))
        print(f"{label:<12} {ic:>7.3f} {gs:>10.2f} {ns:>8.2f} {ann_turn:>8.0f}x "
              f"{metrics.cagr(net.returns, 252):>7.1%} {oos_s:>8.2f} {dsr:>10.2%}")

    # Baseline: equal-weight factor blend.
    baseline = FactorStrategy(features, sectors=sectors, params=FactorStrategyParams(quantile=0.2))
    baseline._is_ic = float("nan")  # type: ignore[attr-defined]
    report("equal-blend", baseline, baseline)

    # ML models: train + save + version, served (in-sample) + refit (walk-forward).
    best: tuple[str, float, int] | None = None
    for name, spec in specs.items():
        est, m = train_model(spec, features, prices, universe, horizon=HORIZON, sectors=sectors)
        card = repo.save(f"alpha101-{name}", est, framework="sklearn",
                         params={"spec": spec.model_dump()}, features=kept,
                         train_span=(str(prices.index.min().date()), str(prices.index.max().date())),
                         metrics=m, lineage={"universe": universe.name, "horizon": HORIZON})
        served = ModelStrategy(features, estimator=est, sectors=sectors,
                               params=ModelStrategyParams(horizon=HORIZON, quantile=0.2))
        served._is_ic = m["ic_mean"]  # type: ignore[attr-defined]
        refit = ModelStrategy(features, spec=spec, sectors=sectors,
                              params=ModelStrategyParams(horizon=HORIZON, quantile=0.2, refit=True,
                                                         embargo=WF.test_days + WF.purge_days))
        report(f"ml-{name}", served, refit)
        oos = concat_oos(walk_forward(refit, prices, universe, eng_net, WF))
        oos_s = metrics.sharpe(oos, periods_per_year=252) if len(oos) else float("-inf")
        if best is None or oos_s > best[1]:
            best = (card.name, oos_s, card.version)

    if best is not None:
        repo.promote(best[0], best[2], ModelStage.PRODUCTION)
        print(f"\nPromoted {best[0]} v{best[2]} → PRODUCTION (best OOS net Sharpe {best[1]:.2f}).")
    print(f"\nDSR = P(true Sharpe > 0) after deflating for the {len(alphas)} alphas searched. "
          "OOS_net is the honest, per-fold-refit walk-forward Sharpe — the number that matters.")


if __name__ == "__main__":
    main()
