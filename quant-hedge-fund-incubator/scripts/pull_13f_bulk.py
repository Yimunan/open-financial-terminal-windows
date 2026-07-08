"""Populate the 13F holdings lake for the **top-N managers over the last ~5 years**, from the SEC
Form 13F bulk Data Sets (one ZIP per filing window — see providers/thirteenf_bulk.py).

  .venv\\Scripts\\python.exe scripts\\pull_13f_bulk.py            # top 100, ~21 quarters
  .venv\\Scripts\\python.exe scripts\\pull_13f_bulk.py 50         # top 50

Steps: (1) rank the latest dataset by long-equity AUM → fixed top-N roster (config/managers_13f_top100.yaml);
(2) for every dataset, write each roster manager's quarter holdings into lake/ownership/13f/<cik>/<period>.parquet
(reusing HoldingsStore, so the graph builder works unchanged); (3) catalog.refresh().
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.data.holdings import HoldingsStore
from qhfi.data.lake import lake_root
from qhfi.data.providers.thirteenf_bulk import ThirteenFBulkClient

# Datasets covering filings for periods 2021-03-31 → 2026-03-31 (filed up to 45 days after q-end).
DATASETS = [
    "2021q2", "2021q3", "2021q4", "2022q1", "2022q2", "2022q3", "2022q4",
    "2023q1", "2023q2", "2023q3", "2023q4",
    "01jan2024-29feb2024", "01mar2024-31may2024", "01jun2024-31aug2024", "01sep2024-30nov2024",
    "01dec2024-28feb2025", "01mar2025-31may2025", "01jun2025-31aug2025", "01sep2025-30nov2025",
    "01dec2025-28feb2026", "01mar2026-31may2026",
]
MIN_PERIOD = "2021-03-31"
ROSTER_YAML = Path("config/managers_13f_top100.yaml")


def main() -> None:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    client = ThirteenFBulkClient(lake_root() / "ownership" / "_13f_datasets")
    store = HoldingsStore(lake_root())

    # 1. fixed roster = top-N by long-equity AUM in the most recent dataset
    print(f"ranking top {top_n} managers from {DATASETS[-1]} ...")
    top = client.rank(DATASETS[-1], top=top_n)
    roster = {int(r.cik): r.manager for r in top.itertuples(index=False)}
    ROSTER_YAML.parent.mkdir(parents=True, exist_ok=True)
    ROSTER_YAML.write_text(yaml.safe_dump(
        {"name": "managers_13f_top100",
         "anchor_period": top["period"].iloc[0],
         "managers": [{"cik": c, "name": n} for c, n in roster.items()]},
        sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"roster → {ROSTER_YAML}  ({len(roster)} managers, anchor {top['period'].iloc[0]})\n")

    # 2. populate the lake, chronologically so later (amended/late) filings overwrite earlier ones
    topset = set(roster)
    files = 0
    for ds in DATASETS:
        h = client.holdings(ds, ciks=topset)
        if h.empty:
            print(f"  {ds:<22} (no roster holdings)")
            continue
        h = h[h["period"] >= MIN_PERIOD]
        n_ds = 0
        for (cik, period), g in h.groupby(["cik", "period"]):
            latest_acc = g.sort_values("filed")["accession"].iloc[-1]      # one filing per q
            g = g[g["accession"] == latest_acc]
            name = roster.get(int(cik), str(g["manager"].iloc[0]))
            # HoldingsStore.save re-inserts manager_cik + manager → drop the provider's own copies
            store.save(int(cik), name, period, g.drop(columns=["cik", "manager"]))
            n_ds += 1
        files += n_ds
        print(f"  {ds:<22} {n_ds:>4} manager-quarters")

    print(f"\nDONE: {files} manager-quarter files written for {len(roster)} managers.")
    from qhfi.data.catalog import refresh
    refresh()


if __name__ == "__main__":
    main()
