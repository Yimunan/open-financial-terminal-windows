"""Evaluate the Alpha101 starter set on a real equity pool: build market panels from the
lake, optionally sector-neutralize each alpha, and rank them by information coefficient.

  .venv\\Scripts\\python.exe scripts\\eval_alpha101.py [pool.yaml]   (default equity_sectors)
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.factors import evaluation as fe
from qhfi.factors import transforms as tf
from qhfi.factors.alpha101 import ALL_ALPHAS
from qhfi.factors.market import MarketPanels

POOL = sys.argv[1] if len(sys.argv) > 1 else "config/instruments/equity_sectors.yaml"


def main() -> None:
    universe = load_universe(POOL)
    store = market_store()
    panels = MarketPanels.from_store(store, universe)
    close = panels.close
    sectors = universe.groups("gics_sector")
    print(f"Pool: {universe.name} | panel {close.shape[0]}d × {close.shape[1]} names "
          f"({close.index.min().date()} → {close.index.max().date()})\n")

    rows = []
    for cls in ALL_ALPHAS:
        alpha = cls(panels)
        raw = alpha.compute(close, universe)
        signal = tf.neutralize(tf.zscore(raw), sectors)        # sector-neutral, standardized
        ic1 = fe.ic_summary(fe.information_coefficient(signal, close, horizon=1))
        ic5 = fe.ic_summary(fe.information_coefficient(signal, close, horizon=5))
        qret = fe.quantile_returns(signal, close, q=5, horizon=5)
        rows.append({
            "alpha": cls.name,
            "IC_1d": round(ic1.mean_ic, 4),
            "IC_5d": round(ic5.mean_ic, 4),
            "IC_IR_5d": round(ic5.ic_ir, 3),
            "t_stat_5d": round(ic5.t_stat, 1),
            "hit_5d": round(ic5.hit_rate, 2),
            "Q5-Q1_5d": round(fe.spread(qret), 4),
            "n": ic5.n,
        })

    df = pd.DataFrame(rows).sort_values("IC_IR_5d", key=lambda s: s.abs(), ascending=False)
    print("Alpha101 starter set — sector-neutral IC (ranked by |5d IC-IR|):")
    print(df.to_string(index=False))
    print("\nNote: signs are arbitrary (an alpha with negative IC is just the inverse signal).")
    print("yfinance prices are adjusted close-only → intraday alphas (open/high/low) use")
    print("adjusted OHLC, a known approximation; true intraday VWAP is not available.")


if __name__ == "__main__":
    main()
