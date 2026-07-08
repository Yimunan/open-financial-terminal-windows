"""Visualization + export for the manager graph.

Reuses ``factors.heatmap.render_heatmap`` for the manager×manager similarity matrix (no new
plotting dependency) and emits a networkx-compatible node-link JSON for external graph tools.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console

from qhfi.factors.heatmap import render_heatmap
from qhfi.ownership.metrics import adjacency

# self-relationship filled on the matrix diagonal at render time (never stored as an edge row)
_DIAG = {"cosine": 1.0, "jaccard": 1.0, "shared_n": np.nan, "shared_usd": np.nan}


def manager_similarity_matrix(store, period: str, metric: str = "cosine") -> pd.DataFrame:
    """Square, symmetric manager×manager matrix for one quarter, labelled by manager name with the
    diagonal filled (1.0 for cosine/jaccard, NaN for the size metrics)."""
    nodes = store.load_nodes(period)
    ciks = list(nodes["cik"])
    names = dict(zip(nodes["cik"], nodes["manager"]))
    A = adjacency(store.load_edges(period), ciks, weight=metric)
    diag = _DIAG.get(metric, 1.0)
    for c in ciks:
        A.at[c, c] = diag
    A.index = [names[c] for c in A.index]
    A.columns = [names[c] for c in A.columns]
    return A


def render_graph_heatmap(store, period: str, metric: str = "cosine",
                         console: Console | None = None) -> None:
    """Print the manager-similarity matrix as a colored Rich heatmap."""
    df = manager_similarity_matrix(store, period, metric)
    center = 0.5 if metric in ("cosine", "jaccard") else 0.0
    render_heatmap(df, f"manager {metric} · {period}", center=center, fmt="{:.2f}",
                   label_width=18, console=console)


def to_node_link(store, period: str, *, metric: str = "cosine", min_weight: float = 0.0) -> dict:
    """networkx ``node_link_data``-compatible dict for one quarter's graph (undirected).

    Edges with ``metric`` below ``min_weight`` are dropped (declutter). Anyone with networkx can
    ``nx.node_link_graph(json.load(...))`` and run community detection / layout externally.
    """
    nodes = store.load_nodes(period)
    edges = store.load_edges(period)
    node_list = [{"id": int(r.cik), "manager": r.manager,
                  "eigenvector_cent": float(r.eigenvector_cent), "degree": int(r.degree),
                  "weighted_degree": float(r.weighted_degree), "n_positions": int(r.n_positions),
                  "value_usd_bn": float(r.value_usd_bn)} for r in nodes.itertuples(index=False)]
    links = []
    for r in edges.itertuples(index=False):
        w = float(getattr(r, metric))
        if w < min_weight:
            continue
        links.append({"source": int(r.cik_a), "target": int(r.cik_b), "weight": w,
                      "cosine": float(r.cosine), "jaccard": float(r.jaccard),
                      "shared_n": int(r.shared_n), "shared_usd": float(r.shared_usd)})
    return {"period": period, "metric": metric, "directed": False, "multigraph": False,
            "nodes": node_list, "links": links}


def write_node_link(store, period: str, out_path, *, metric: str = "cosine",
                    min_weight: float = 0.0) -> Path:
    """Write the node-link JSON for one quarter to ``out_path``."""
    data = to_node_link(store, period, metric=metric, min_weight=min_weight)
    out = Path(out_path)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out
