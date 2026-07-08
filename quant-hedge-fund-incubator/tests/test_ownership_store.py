"""ManagerGraphStore persistence + time-series access (qhfi.ownership.store)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.ownership.graph import EDGE_COLS, NODE_COLS
from qhfi.ownership.store import ManagerGraphStore


def _edges(period: str, triples) -> pd.DataFrame:
    rows = [{"period": period, "cik_a": a, "cik_b": b, "manager_a": f"M{a}", "manager_b": f"M{b}",
             "cosine": w, "jaccard": w, "shared_n": 1, "shared_usd": 1.0, "filed": "2024-08-14"}
            for a, b, w in triples]
    return pd.DataFrame(rows, columns=EDGE_COLS)


def _nodes(period: str, ciks) -> pd.DataFrame:
    rows = [{"period": period, "cik": c, "manager": f"M{c}", "filed": "2024-08-14",
             "degree": 1, "weighted_degree": 1.0, "eigenvector_cent": 1.0,
             "n_positions": 10, "value_usd_bn": 1.0} for c in ciks]
    return pd.DataFrame(rows, columns=NODE_COLS)


def test_roundtrip_and_paths(tmp_path):
    store = ManagerGraphStore(tmp_path)
    e, n = _edges("2024-06-30", [(1, 2, 0.4)]), _nodes("2024-06-30", [1, 2])
    assert store.save_edges("2024-06-30", e) == 1
    assert store.save_nodes("2024-06-30", n) == 2
    assert store.has("2024-06-30")
    assert store._edge_path("2024-06-30").parts[-2:] == ("manager_graph", "2024-06-30.parquet")
    assert store._node_path("2024-06-30").parts[-2:] == ("manager_graph_nodes", "2024-06-30.parquet")
    pd.testing.assert_frame_equal(store.load_edges("2024-06-30"), e)
    pd.testing.assert_frame_equal(store.load_nodes("2024-06-30"), n)


def test_periods_sorted(tmp_path):
    store = ManagerGraphStore(tmp_path)
    for p in ("2024-06-30", "2023-12-31", "2024-03-31"):
        store.save_edges(p, _edges(p, [(1, 2, 0.1)]))
    assert store.periods() == ["2023-12-31", "2024-03-31", "2024-06-30"]


def test_edge_series_nan_when_not_cofiled(tmp_path):
    store = ManagerGraphStore(tmp_path)
    store.save_edges("2024-03-31", _edges("2024-03-31", [(1, 2, 0.3)]))   # both filed
    store.save_edges("2024-06-30", _edges("2024-06-30", []))              # pair absent
    s = store.edge_series(1, 2, "cosine")
    assert isinstance(s.index, pd.DatetimeIndex)
    assert s.iloc[0] == 0.3
    assert np.isnan(s.iloc[1])


def test_edge_series_handles_arg_order(tmp_path):
    store = ManagerGraphStore(tmp_path)
    store.save_edges("2024-06-30", _edges("2024-06-30", [(1, 2, 0.6)]))
    assert store.edge_series(2, 1).iloc[0] == 0.6   # canonicalized to (1,2)
