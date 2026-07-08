"""Pull recent SEC Form 13F-HR institutional holdings for a curated set of managers into
lake/ownership/13f/. One parquet per manager-quarter.

  .venv\\Scripts\\python.exe scripts\\pull_13f.py            # latest 2 quarters each
  .venv\\Scripts\\python.exe scripts\\pull_13f.py 4          # latest 4 quarters each
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.data.holdings import HoldingsStore
from qhfi.data.lake import lake_root
from qhfi.data.providers.thirteenf import ThirteenFClient

POOL = Path("config/managers_13f.yaml")


def main() -> None:
    quarters = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    managers = yaml.safe_load(POOL.read_text())["managers"]
    client, store = ThirteenFClient(), HoldingsStore(lake_root())
    print(f"13F-HR holdings → {store.data_dir.resolve()}  (latest {quarters} quarter(s) × "
          f"{len(managers)} managers)\n")

    files = 0
    for m in managers:
        cik, name = m["cik"], m["name"]
        try:
            filings = client.list_13f(cik)[:quarters]
        except Exception as e:                                  # noqa: BLE001
            print(f"  {name:<26} list ERROR {type(e).__name__}: {e}")
            continue
        if not filings:
            print(f"  {name:<26} no 13F-HR found")
            continue
        for f in filings:
            if store.has(cik, f.report_date):
                print(f"  {name:<26} {f.report_date}  (cached)")
                continue
            try:
                holdings = client.fetch_holdings(f)
            except Exception as e:                              # noqa: BLE001
                print(f"  {name:<26} {f.report_date}  fetch ERROR {type(e).__name__}: {e}")
                continue
            if holdings.empty:
                print(f"  {name:<26} {f.report_date}  (no XML info table — skipped)")
                continue
            n = store.save(cik, name, f.report_date, holdings)
            aum = holdings["value_usd"].sum() / 1e9
            files += 1
            print(f"  {name:<26} {f.report_date}  filed {f.filing_date}  "
                  f"{n:>4} positions  ${aum:,.1f}B")

    cat = store.catalog()
    print(f"\nDONE: {files} new manager-quarter files  ({len(cat)} total in lake)")
    if not cat.empty:
        print("\ncatalog (latest per manager):")
        latest = cat.sort_values("period").groupby("manager").tail(1).sort_values("value_usd_bn", ascending=False)
        print(latest.to_string(index=False))
    print("\n13F = long US 13(f) securities only (no shorts/cash/non-US). PIT anchor = filing date "
          "(positions as-of quarter-end, up to 45-day lag).")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
