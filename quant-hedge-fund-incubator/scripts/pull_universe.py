"""Lean, resumable bulk pull for large universes (thousands of names).

  .venv\\Scripts\\python.exe scripts\\pull_universe.py <pool.yaml> <lake_dir>

Ingests via DataManager (incremental: cached names skip, failures retry on re-run) and prints
a concise progress summary every N names + final counts. No full-panel assembly (too heavy at
this scale). Designed to run in the background and be re-run to fill gaps.
"""

from __future__ import annotations

import sys
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.types import AssetClass, DateRange
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.data.manager import DataManager
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider

SPAN = DateRange(start=date(2000, 1, 1), end=date(2026, 6, 4))


def main() -> None:
    pool = sys.argv[1]
    universe = load_universe(pool)
    store = market_store()
    mgr = DataManager(store, {AssetClass.EQUITY: YFinanceDataProvider()}, max_daily_return=0.5)
    n = len(universe.instruments)
    print(f"Pulling {n} names from {pool} → {store.data_dir.resolve()}", flush=True)

    stored = skipped = errors = norows = 0
    err_samples: list[str] = []
    for i, ins in enumerate(universe.instruments, 1):
        rep = mgr.update(type(universe)(name="_", instruments=[ins]), SPAN)[0]
        if rep.fetched_rows:
            stored += 1
        elif rep.skipped_reason == "up-to-date":
            skipped += 1
        elif rep.skipped_reason and "error" in rep.skipped_reason:
            errors += 1
            if len(err_samples) < 15:
                err_samples.append(f"{rep.instrument_id}: {rep.skipped_reason}")
        else:
            norows += 1
        if i % 200 == 0:
            print(f"  [{i}/{n}] stored={stored} cached={skipped} norows={norows} errors={errors}",
                  flush=True)

    print(f"\nDONE: {stored} stored · {skipped} cached · {norows} no-rows · {errors} errors")
    for s in err_samples:
        print(f"  err {s}")
    files = list(store.data_dir.glob("*/*.parquet"))
    print(f"Lake now: {len(files)} parquet files")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
