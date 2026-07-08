"""Build the institutional-manager relationship graph from the 13F holdings lake — one edge-list
+ one node-metric parquet per quarter under lake/ownership/manager_graph[_nodes]/.

  .venv\\Scripts\\python.exe scripts\\build_manager_graph.py

Reads lake/ownership/13f (populated by scripts/pull_13f.py); for ≥4 quarters of holdings the
relationship evolution becomes visible (`qhfi ownership changes`).
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path

import yaml

from qhfi.data.holdings import HoldingsStore
from qhfi.data.lake import lake_root
from qhfi.ownership.graph import build_all
from qhfi.ownership.store import ManagerGraphStore

ROSTER_YAML = Path("config/managers_13f_top100.yaml")


def _roster() -> set[int] | None:
    """Fixed manager universe (top-N) if pull_13f_bulk.py wrote one; else None (use all in lake)."""
    if not ROSTER_YAML.exists():
        return None
    return {int(m["cik"]) for m in yaml.safe_load(ROSTER_YAML.read_text())["managers"]}


def main() -> None:
    store, graph = HoldingsStore(lake_root()), ManagerGraphStore(lake_root())
    ciks = _roster()
    print(f"manager graph → {graph.edge_dir.resolve()}"
          f"{f'  (roster: {len(ciks)} managers)' if ciks else ''}\n")
    periods = build_all(store, graph, ciks=ciks)
    if not periods:
        print("no 13F holdings in the lake — run scripts/pull_13f.py first.")
        return

    print(f"{'period':<12} {'nodes':>6} {'edges':>6} {'mean cos':>9}  top-central")
    for p in periods:
        edges, nodes = graph.load_edges(p), graph.load_nodes(p)
        mean_cos = edges["cosine"].mean() if not edges.empty else float("nan")
        top = (nodes.sort_values("eigenvector_cent", ascending=False)["manager"].iloc[0]
               if not nodes.empty else "—")
        print(f"{p:<12} {len(nodes):>6} {len(edges):>6} {mean_cos:>9.3f}  {top}")

    print(f"\nDONE: {len(periods)} quarterly snapshots. "
          "Edge weight = portfolio-overlap cosine (value-weight vectors over shared CUSIPs).")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
