"""Pull raw financial statements (income / balance / cash flow, quarterly + annual) for a
universe into the new `statements` lake category.

  .venv\\Scripts\\python.exe scripts\\pull_statements.py [pool.yaml]   (default equity_sectors)
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.universe_io import load_universe
from qhfi.data.lake import lake_root
from qhfi.data.providers.statements_yfinance import FREQS, STATEMENTS, YFinanceStatementsProvider
from qhfi.data.statements import StatementsStore

POOL = sys.argv[1] if len(sys.argv) > 1 else "config/instruments/equity_sectors.yaml"


def main() -> None:
    universe = load_universe(POOL)
    provider = YFinanceStatementsProvider()
    store = StatementsStore(lake_root())
    print(f"Pulling statements for {len(universe.instruments)} names → {store.data_dir.resolve()}")

    saved, empty, errors = 0, 0, 0
    for i, ins in enumerate(universe.instruments, 1):
        for statement in STATEMENTS:
            for freq in FREQS:
                try:
                    df = provider.fetch(ins, statement, freq)
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    if errors <= 10:
                        print(f"  {ins.id} {statement}/{freq}: {type(e).__name__}")
                    continue
                if df.empty:
                    empty += 1
                else:
                    store.save(ins, statement, freq, df)
                    saved += 1
        if i % 25 == 0:
            print(f"  [{i}/{len(universe.instruments)}] saved={saved} empty={empty} errors={errors}", flush=True)

    print(f"\nDONE: {saved} statement files saved · {empty} empty · {errors} errors")
    cat = store.catalog()
    print(f"\nCatalog: {len(cat)} files across {cat['category'].nunique()} categories")
    print(cat.groupby("category").agg(files=("id", "count"), avg_periods=("periods", "mean")).round(1).to_string())
    print("\nSample (AAPL quarterly income, latest 2 periods × 6 lines):")
    from qhfi.core.types import AssetClass, Instrument
    aapl = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)
    if store.has(aapl, "income", "quarterly"):
        df = store.load(aapl, "income", "quarterly")
        print(df.tail(2).iloc[:, :6].to_string())


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
