"""Render the four factor heatmaps for the Alpha101 starter set on a real equity pool:
correlation (collinearity), IC-over-time (stability), IC scorecard, and IC decay.

  .venv\\Scripts\\python.exe scripts\\factor_heatmap.py [pool.yaml]   (default equity_sectors)
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from rich.console import Console

from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.factors import heatmap as hm
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

    # Standardized, sector-neutral signals — same prep as eval_alpha101.
    signals = {
        cls.name: tf.neutralize(tf.zscore(cls(panels).compute(close, universe)), sectors)
        for cls in ALL_ALPHAS
    }

    print("Legend: green = above center / positive, red = below / negative.\n")

    # Wide console so labels/cells aren't clipped to the default 80 cols. Size for the
    # cross-sector matrix, which has the most columns (one per GICS sector).
    n_sectors = len(set(sectors.values()))
    console = Console(width=max(120, 16 + 13 * n_sectors))

    # Cross-asset structure: correlation of equal-weight sector-basket returns. (For a true
    # multi-asset universe, pass an asset_class map instead of the gics_sector groups.)
    hm.render_heatmap(hm.asset_correlation(close, sectors),
                      "Cross-sector return correlation (Pearson)", label_width=12, console=console)
    print()
    hm.render_heatmap(hm.factor_correlation(signals), "Factor correlation (Spearman)", console=console)
    print()
    hm.render_heatmap(hm.ic_over_time(signals, close, horizon=5, freq="YE"),
                      "Mean IC over time (5d, yearly)", console=console)
    print()
    hm.render_heatmap(hm.ic_scorecard(signals, close, horizon=5), "IC scorecard (5d)",
                      per_column=True, console=console)
    print()
    hm.render_heatmap(hm.ic_decay_matrix(signals, close), "IC decay by horizon", console=console)

    print("\nNote: signs are arbitrary (a factor with negative IC is just the inverse signal);")
    print("collinear alphas surface as a bright off-diagonal block in the correlation matrix.")


if __name__ == "__main__":
    main()
