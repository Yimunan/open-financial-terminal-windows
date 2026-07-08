"""Out-of-sample validation — the defense against overfitting that gates promotion.

Walk-forward splits time into rolling train/test windows; a **purge gap** between train and
test drops samples whose lookback would straddle the boundary (preventing leakage from
overlapping windows). For each fold the strategy sees prices only up to the test window's
end (it is causal, and the engine's one-bar lag is the look-ahead guard); we then keep only
the **test-window** slice of the result. Stitched together, the test slices form a single
contiguous out-of-sample track — which the scorecard compares against in-sample Sharpe.

For a parameter-free daily strategy this is effectively honest OOS slicing; for a *fitted*
strategy you would refit on each train window before evaluating its test window (the hook is
the per-fold `strategy.generate_weights` call — swap in a refit there).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from qhfi.backtest.engine import BacktestEngine, BacktestResult
from qhfi.core.types import Panel, Universe
from qhfi.strategy.base import Strategy


@dataclass
class WalkForwardConfig:
    train_days: int = 504        # ~2y
    test_days: int = 126         # ~6m
    step_days: int = 126         # roll forward by one test window (== test_days → contiguous OOS)
    purge_days: int = 10         # drop samples adjacent to the split


def _slice_to_window(res: BacktestResult, idx: pd.Index, initial_equity: float) -> BacktestResult:
    """Restrict a full-fold result to its test window, rebasing equity from returns."""
    idx = res.returns.index.intersection(idx)
    ret = res.returns.reindex(idx).fillna(0.0)
    equity = (1 + ret).cumprod() * initial_equity

    def sl(s: pd.Series) -> pd.Series:
        return s.reindex(idx)

    trades = res.trades
    if trades is not None and len(trades):
        trades = trades[trades["date"].isin(idx)]

    return BacktestResult(
        equity_curve=equity, returns=ret,
        weights=res.weights.reindex(idx), turnover=sl(res.turnover), costs=sl(res.costs),
        meta={**res.meta, "oos_window": (str(idx[0].date()), str(idx[-1].date()))},
        cash=sl(res.cash), gross_exposure=sl(res.gross_exposure), net_exposure=sl(res.net_exposure),
        commission=sl(res.commission), slippage=sl(res.slippage), financing=sl(res.financing),
        carry=sl(res.carry), positions=res.positions.reindex(idx), trades=trades,
    )


def walk_forward(
    strategy: Strategy,
    prices: Panel,
    universe: Universe,
    engine: BacktestEngine,
    cfg: WalkForwardConfig | None = None,
) -> list[BacktestResult]:
    """Run the strategy across rolling OOS windows; return one (test-window-only) result per
    fold. Each fold's signals use data only through that fold's test-window end."""
    cfg = cfg or WalkForwardConfig()
    prices = prices.sort_index()
    n = len(prices)
    results: list[BacktestResult] = []

    pos = cfg.train_days
    while pos + cfg.purge_days + cfg.test_days <= n:
        test_start = pos + cfg.purge_days
        test_end = test_start + cfg.test_days
        fold_prices = prices.iloc[:test_end]                       # causal view
        weights = strategy.generate_weights(fold_prices, universe)
        full = engine.run(weights, fold_prices, universe)
        test_idx = prices.index[test_start:test_end]
        results.append(_slice_to_window(full, test_idx, engine.initial_equity))
        pos += cfg.step_days

    return results


def concat_oos(results: list[BacktestResult]) -> pd.Series:
    """Stitch the per-fold test-window net returns into one contiguous OOS return series."""
    if not results:
        return pd.Series(dtype=float)
    rets = pd.concat([r.returns for r in results])
    return rets[~rets.index.duplicated(keep="first")].sort_index()
