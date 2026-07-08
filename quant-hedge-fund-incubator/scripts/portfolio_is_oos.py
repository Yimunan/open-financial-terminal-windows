"""Factor-free portfolio allocation: in-sample vs out-of-sample backtest, benchmarked against
the long-only 1/N portfolio. Monthly rebalancing, weights drift between rebalances.

  IN-SAMPLE : weights estimated on the FULL sample, then scored on it (look-ahead → optimistic).
  OUT-OF-SAMPLE: at each month-end, weights from a trailing window only, held next month (honest).

  .venv\\Scripts\\python.exe scripts\\portfolio_is_oos.py
"""

from __future__ import annotations

import sys
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.evaluation import metrics
from qhfi.portfolio.allocations import ALLOCATORS

START = date(2014, 1, 1)
LOOKBACK = 504        # ~2y trailing estimation window for OOS


def month_starts(index: pd.DatetimeIndex) -> list:
    return [index[i] for i in range(len(index)) if i == 0 or index[i].month != index[i - 1].month]


def run(schedule: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """Daily portfolio returns: reset to target weights on rebalance dates, drift in between."""
    reb = set(schedule.index)
    cols = returns.columns
    w = None
    out: dict = {}
    for dt, row in returns.iterrows():
        r = row.to_numpy()
        if dt in reb:
            w = schedule.loc[dt, cols].to_numpy(dtype=float)
        if w is None:
            continue
        out[dt] = float(np.nansum(w * r))
        w = w * (1 + np.nan_to_num(r))                  # drift with returns
        s = w.sum()
        if s > 0:
            w = w / s
    return pd.Series(out)


def line(name: str, ins: pd.Series, oos: pd.Series) -> str:
    def block(r):
        return (f"{metrics.sharpe(r, periods_per_year=252):>6.2f} {metrics.cagr(r,252):>7.1%} "
                f"{metrics.ann_vol(r,252):>6.1%} {metrics.max_drawdown(r):>7.1%}")
    return f"{name:<16} | {block(ins)} | {block(oos)}"


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    prices = market_store().load_panel(universe.instruments, "close")
    prices = prices[prices.index >= pd.Timestamp(START, tz="UTC")].dropna(axis=1, how="any")
    returns = prices.pct_change().dropna(how="all")
    reb = month_starts(returns.index)
    print(f"Universe: {prices.shape[1]} names (full history) | {returns.index.min().date()} → "
          f"{returns.index.max().date()} | {len(reb)} monthly rebalances | OOS lookback {LOOKBACK}d\n")
    print(f"{'method':<16} | {'-- IN-SAMPLE --':^29} | {'-- OUT-OF-SAMPLE --':^29}")
    print(f"{'':<16} | {'Sharpe':>6} {'CAGR':>7} {'vol':>6} {'maxDD':>7} | "
          f"{'Sharpe':>6} {'CAGR':>7} {'vol':>6} {'maxDD':>7}")

    for name, fn in ALLOCATORS.items():
        # in-sample: one set of weights from the whole sample, rebalanced monthly to it
        w_full = pd.Series(fn(returns), index=returns.columns)
        is_sched = pd.DataFrame([w_full] * len(reb), index=reb)
        is_ret = run(is_sched, returns)

        # out-of-sample: trailing-window weights at each rebalance, held to the next
        rows, dates = [], []
        for t in reb:
            hist = returns.loc[:t].iloc[:-1].tail(LOOKBACK)
            if len(hist) < LOOKBACK:
                continue
            rows.append(fn(hist)); dates.append(t)
        oos_sched = pd.DataFrame(rows, index=dates, columns=returns.columns)
        oos_ret = run(oos_sched, returns).loc[oos_sched.index.min():]

        print(line(name, is_ret, oos_ret))

    print("\nIn-sample uses full-sample weights (look-ahead). Out-of-sample re-estimates from a")
    print("trailing window only. Gross of costs. Watch how optimized methods' Sharpe decays IS→OOS")
    print("while 1/N (no estimation) barely moves — the classic estimation-error result.")


if __name__ == "__main__":
    main()
