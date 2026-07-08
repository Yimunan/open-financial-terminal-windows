"""Heatmap matrix + node-link export (qhfi.ownership.viz)."""

from __future__ import annotations

import json
from io import StringIO

import numpy as np
import pandas as pd
from rich.console import Console

from qhfi.ownership.graph import EDGE_COLS, NODE_COLS
from qhfi.ownership.store import ManagerGraphStore
from qhfi.ownership.viz import manager_similarity_matrix, render_graph_heatmap, to_node_link


def _seed(tmp_path) -> ManagerGraphStore:
    store = ManagerGraphStore(tmp_path)
    edges = pd.DataFrame([
        {"period": "2024-06-30", "cik_a": 1, "cik_b": 2, "manager_a": "Alpha", "manager_b": "Beta",
         "cosine": 0.8, "jaccard": 0.5, "shared_n": 3, "shared_usd": 1.0, "filed": "2024-08-14"},
        {"period": "2024-06-30", "cik_a": 1, "cik_b": 3, "manager_a": "Alpha", "manager_b": "Gamma",
         "cosine": 0.2, "jaccard": 0.1, "shared_n": 1, "shared_usd": 0.5, "filed": "2024-08-14"},
        {"period": "2024-06-30", "cik_a": 2, "cik_b": 3, "manager_a": "Beta", "manager_b": "Gamma",
         "cosine": 0.4, "jaccard": 0.3, "shared_n": 2, "shared_usd": 0.7, "filed": "2024-08-14"},
    ], columns=EDGE_COLS)
    nodes = pd.DataFrame([
        {"period": "2024-06-30", "cik": c, "manager": m, "filed": "2024-08-14", "degree": 2,
         "weighted_degree": 1.0, "eigenvector_cent": e, "n_positions": 5, "value_usd_bn": 2.0}
        for c, m, e in [(1, "Alpha", 1.0), (2, "Beta", 0.9), (3, "Gamma", 0.6)]
    ], columns=NODE_COLS)
    store.save_edges("2024-06-30", edges)
    store.save_nodes("2024-06-30", nodes)
    return store


def test_similarity_matrix_square_symmetric_named(tmp_path):
    A = manager_similarity_matrix(_seed(tmp_path), "2024-06-30", "cosine")
    assert A.shape == (3, 3)
    assert list(A.index) == ["Alpha", "Beta", "Gamma"]
    assert np.allclose(A.to_numpy(), A.to_numpy().T)
    assert np.allclose(np.diag(A.to_numpy()), 1.0)            # cosine self = 1
    assert A.loc["Alpha", "Beta"] == 0.8


def test_render_graph_heatmap_smoke(tmp_path):
    buf = StringIO()
    render_graph_heatmap(_seed(tmp_path), "2024-06-30", "cosine", console=Console(file=buf, width=120))
    out = buf.getvalue()
    assert "2024-06-30" in out and "Alpha" in out


def test_to_node_link_shape_and_serializable(tmp_path):
    g = to_node_link(_seed(tmp_path), "2024-06-30", metric="cosine")
    assert g["directed"] is False and g["metric"] == "cosine"
    assert len(g["nodes"]) == 3
    assert len(g["links"]) == 3                              # n*(n-1)/2 with all 3 pairs present
    json.dumps(g)                                            # must be JSON-serializable


def test_to_node_link_min_weight_filters(tmp_path):
    g = to_node_link(_seed(tmp_path), "2024-06-30", metric="cosine", min_weight=0.3)
    assert len(g["links"]) == 2                              # drops the 0.2 edge
