"""Institutional manager relationship graph, derived from SEC 13F holdings.

Nodes = 13F managers; edges = portfolio-overlap similarity (primary weight = value-weight cosine).
One graph snapshot per quarter, so manager clusters and ties can be tracked over time.
"""

from __future__ import annotations

from qhfi.ownership.dashboard import dashboard_data, write_dashboard
from qhfi.ownership.graph import build_all, build_snapshot, pair_edge, value_vector, weight_vector
from qhfi.ownership.metrics import node_metrics, relationship_deltas
from qhfi.ownership.store import ManagerGraphStore
from qhfi.ownership.viz import manager_similarity_matrix, render_graph_heatmap, to_node_link

__all__ = [
    "ManagerGraphStore", "build_all", "build_snapshot", "pair_edge", "value_vector",
    "weight_vector", "node_metrics", "relationship_deltas", "manager_similarity_matrix",
    "render_graph_heatmap", "to_node_link", "dashboard_data", "write_dashboard",
]
