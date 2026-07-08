"""Pull the curated equity-sector pool (max history) into the parquet lake via DataManager,
then report ingestion, ragged listing dates, and the assembled panel.

  .venv\\Scripts\\python.exe scripts\\pull_equity_pool.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.types import AssetClass, DateRange
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.data.manager import DataManager
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider

POOL = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config") / "instruments" / "equity_sectors.yaml"
SPAN = DateRange(start=date(2000, 1, 1), end=date(2026, 6, 4))   # "max available"


def main() -> None:
    universe = load_universe(POOL)
    store = market_store()
    mgr = DataManager(store, {AssetClass.EQUITY: YFinanceDataProvider()}, max_daily_return=0.5)
    print(f"Pool: {universe.name} ({len(universe.instruments)} names)  →  lake {store.data_dir.resolve()}")

    reports = mgr.update(universe, SPAN)
    fetched = sum(r.fetched_rows for r in reports)
    skipped = [r for r in reports if r.skipped_reason]
    flagged = [r for r in reports if r.issues]
    print(f"\nIngest: {fetched:,} rows · {len(reports) - len(skipped)} stored · {len(skipped)} skipped")
    for r in skipped:
        print(f"  SKIP {r.instrument_id:<6} {r.skipped_reason}")
    for r in flagged:
        print(f"  ⚠   {r.instrument_id:<6} {r.issues}")

    cat = mgr.catalog().sort_values("start")
    print(f"\nCatalog ({len(cat)} instruments) — earliest & latest listings:")
    print(cat.head(6).to_string(index=False))
    print("  ...")
    print(cat.tail(4).to_string(index=False))

    panel = mgr.get_panel(universe, "close", SPAN)
    print(f"\nPanel: {panel.shape[0]:,} days × {panel.shape[1]} names  "
          f"({panel.index.min().date()} → {panel.index.max().date()})")
    # ragged starts: how many names have data at each point
    coverage_start = panel.notna().sum(axis=1)
    full = int((coverage_start == panel.shape[1]).sum())
    print(f"Fully-populated rows (all names present): {full:,} of {panel.shape[0]:,}")
    print(f"Names present at panel start: {int(coverage_start.iloc[0])} / {panel.shape[1]}")
    print(f"Names present at panel end:   {int(coverage_start.iloc[-1])} / {panel.shape[1]}")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
