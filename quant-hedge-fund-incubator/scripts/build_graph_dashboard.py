"""Render the self-contained HTML dashboard for the 13F manager relationship graph.

  .venv\\Scripts\\python.exe scripts\\build_graph_dashboard.py                 # → reports/manager_graph.html
  .venv\\Scripts\\python.exe scripts\\build_graph_dashboard.py out.html jaccard # custom path + metric

One standalone .html (all quarters embedded, vis-network via CDN) — opens offline in any browser.
Reads lake/ownership/manager_graph (run scripts/build_manager_graph.py first).
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.data.holdings import HoldingsStore
from qhfi.data.lake import lake_root
from qhfi.ownership.dashboard import write_dashboard
from qhfi.ownership.store import ManagerGraphStore


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "reports/manager_graph.html"
    metric = sys.argv[2] if len(sys.argv) > 2 else "cosine"
    store = ManagerGraphStore(lake_root())
    if not store.periods():
        print("no manager-graph snapshots — run scripts/build_manager_graph.py first.")
        return
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    path = write_dashboard(store, out, holdings_store=HoldingsStore(lake_root()), metric=metric)
    print(f"dashboard → {path.resolve()}  ({len(store.periods())} quarters, metric={metric})")
    print("open it in a browser (needs internet once for the vis-network CDN script).")


if __name__ == "__main__":
    main()
