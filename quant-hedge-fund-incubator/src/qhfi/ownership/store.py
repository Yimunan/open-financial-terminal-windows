"""ManagerGraphStore — the ``ownership/manager_graph`` lake category: the derived 13F manager
relationship graph, one edge-list parquet and one node-metric parquet per quarter.

Layout (sibling to ``ownership/13f``):
    lake/ownership/manager_graph/<period>.parquet        — undirected edge list (cik_a < cik_b)
    lake/ownership/manager_graph_nodes/<period>.parquet  — per-quarter node metrics

``edge_series`` reads one pair's metric across all quarters as a time series (NaN where the pair
did not co-file); ``adjacency_panel`` returns the per-quarter matrices for evolution views.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from qhfi.ownership.metrics import adjacency


class ManagerGraphStore:
    def __init__(self, root) -> None:
        self.edge_dir = Path(root) / "ownership" / "manager_graph"
        self.node_dir = Path(root) / "ownership" / "manager_graph_nodes"

    def _edge_path(self, period: str) -> Path:
        return self.edge_dir / f"{period}.parquet"

    def _node_path(self, period: str) -> Path:
        return self.node_dir / f"{period}.parquet"

    def has(self, period: str) -> bool:
        return self._edge_path(period).exists()

    def periods(self) -> list[str]:
        if not self.edge_dir.exists():
            return []
        return sorted(p.stem for p in self.edge_dir.glob("*.parquet"))

    def save_edges(self, period: str, df: pd.DataFrame) -> int:
        self.edge_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(self._edge_path(period))
        return len(df)

    def save_nodes(self, period: str, df: pd.DataFrame) -> int:
        self.node_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(self._node_path(period))
        return len(df)

    def load_edges(self, period: str) -> pd.DataFrame:
        return pd.read_parquet(self._edge_path(period))

    def load_nodes(self, period: str) -> pd.DataFrame:
        return pd.read_parquet(self._node_path(period))

    def edge_series(self, cik_a: int, cik_b: int, metric: str = "cosine") -> pd.Series:
        """``metric`` for one pair across every quarter, indexed by quarter-end (DatetimeIndex).
        NaN for quarters where the two managers did not both file."""
        a, b = sorted((int(cik_a), int(cik_b)))
        vals: dict[str, float] = {}
        for p in self.periods():
            e = self.load_edges(p)
            m = e[(e["cik_a"] == a) & (e["cik_b"] == b)]
            vals[p] = float(m[metric].iloc[0]) if len(m) else np.nan
        return pd.Series(list(vals.values()), index=pd.to_datetime(list(vals)), name=metric)

    def adjacency_panel(self, metric: str = "cosine") -> dict[str, pd.DataFrame]:
        """period → N×N manager-similarity matrix, for animating the graph's evolution."""
        out: dict[str, pd.DataFrame] = {}
        for p in self.periods():
            nodes = self.load_nodes(p)
            out[p] = adjacency(self.load_edges(p), list(nodes["cik"]), weight=metric)
        return out
