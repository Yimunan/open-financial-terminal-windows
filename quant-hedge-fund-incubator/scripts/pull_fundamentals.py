"""Pull PIT fundamentals (yfinance) for a universe into data/lake/fundamental/, then show a
sample metric panel and a value/quality factor built on it.

  .venv\\Scripts\\python.exe scripts\\pull_fundamentals.py [pool.yaml]   (default dow30)
"""

from __future__ import annotations

import sys
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.types import DateRange
from qhfi.core.universe_io import load_universe
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.lake import lake_root, market_store
from qhfi.data.providers.fundamentals_yfinance import METRICS, YFinanceFundamentalsProvider

POOL = sys.argv[1] if len(sys.argv) > 1 else "config/instruments/dow30.yaml"
SPAN = DateRange(start=date(2000, 1, 1), end=date(2026, 6, 4))


def main() -> None:
    universe = load_universe(POOL)
    provider = YFinanceFundamentalsProvider(reporting_lag_days=60)
    store = FundamentalsStore(lake_root())
    print(f"Pulling fundamentals for {len(universe.instruments)} names → {store.data_dir.resolve()}")

    stored = {m: 0 for m in METRICS}
    for ins in universe.instruments:
        for metric in METRICS:
            try:
                s = provider.fetch(ins, metric, SPAN)
            except Exception as e:  # noqa: BLE001
                print(f"  {ins.id} {metric}: {type(e).__name__}")
                continue
            if len(s):
                store.save(ins, metric, s)
                stored[metric] += len(s)
    print("Stored PIT points per metric:", stored)

    # Sample: ROE panel (sparse, PIT) ffilled to the daily price grid
    roe = store.panel(universe.instruments, "roe")
    print(f"\nROE panel: {roe.shape[0]} report dates × {roe.shape[1]} names")
    if len(roe):
        print(f"  knowable-date range: {roe.index.min().date()} → {roe.index.max().date()}")
        print(roe.tail(3).iloc[:, :6].round(3).to_string())

    # Earnings yield = TTM EPS (PIT, ffilled) / price — combines fundamental + market
    eps = store.panel(universe.instruments, "eps_ttm")
    if len(eps):
        prices = market_store().load_panel(universe.instruments, "close")
        eps_daily = eps.reindex(prices.index, method="ffill")
        ey = (eps_daily / prices).iloc[-1].dropna().sort_values(ascending=False)
        print("\nEarnings yield (TTM EPS / price), top 5 cheapest in Dow:")
        print(ey.head(5).round(4).to_string())


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
