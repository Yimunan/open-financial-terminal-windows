"""Build the CUSIP→ticker crosswalk from the 13F holdings lake via OpenFIGI.

Collects every unique CUSIP across lake/ownership/13f/, maps the ones not already known, and
stores the crosswalk at lake/reference/cusip_ticker.parquet. Resumable (skips known CUSIPs).
Unauthenticated OpenFIGI is slow (~200 CUSIPs/min) — set OPENFIGI_API_KEY to go faster, or cap
the run:  build_cusip_crosswalk.py 500   (map at most 500 new CUSIPs this run).

  .venv\\Scripts\\python.exe scripts\\build_cusip_crosswalk.py
"""

from __future__ import annotations

import sys
import time

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.data.crosswalk import CusipTickerStore
from qhfi.data.holdings import HoldingsStore
from qhfi.data.lake import lake_root
from qhfi.data.providers.openfigi import OpenFigiMapper

_BATCH = 50   # checkpoint to disk every N newly-mapped CUSIPs


def unique_cusips(store: HoldingsStore) -> list[str]:
    seen = set()
    for p in sorted(store.data_dir.glob("*/*.parquet")):
        seen.update(pd.read_parquet(p, columns=["cusip"])["cusip"].dropna().unique())
    return sorted(seen)


def main() -> None:
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else None
    xwalk, mapper = CusipTickerStore(lake_root()), OpenFigiMapper()
    allc = unique_cusips(HoldingsStore(lake_root()))
    known = xwalk.known()
    todo = [c for c in allc if c not in known]
    if cap:
        todo = todo[:cap]
    print(f"unique CUSIPs in 13F lake: {len(allc)}  ·  already mapped: {len(known)}  ·  "
          f"to map now: {len(todo)}\n", flush=True)

    mapped, t0 = 0, time.time()
    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        res = mapper.map(chunk)
        mapped += xwalk.upsert(res)
        print(f"  {min(i + _BATCH, len(todo)):>5}/{len(todo)}  +{len(res)} mapped  "
              f"({time.time() - t0:.0f}s)", flush=True)

    df = xwalk.load()
    print(f"\nDONE: crosswalk now {len(df)} CUSIPs  ({mapped} added this run) → {xwalk.path}")
    if not df.empty:
        print("\nsample:")
        print(df.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
