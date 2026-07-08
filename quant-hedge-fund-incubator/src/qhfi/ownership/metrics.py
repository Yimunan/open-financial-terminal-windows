"""Node-level graph metrics + relationship evolution — pure pandas/numpy, no networkx/scipy.

Given a quarter's edge list, build the symmetric weighted adjacency and derive who is central:
degree (how many ties), weighted degree / strength (sum of tie weights), and eigenvector
centrality via power iteration on the adjacency. ``relationship_deltas`` compares two quarters to
flag emerging / fading / new / dropped manager relationships.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_DELTA_COLS = ["cik_a", "cik_b", "manager_a", "manager_b", "prev", "curr", "delta", "status"]


def adjacency(edges: pd.DataFrame, ciks: list[int], weight: str = "cosine") -> pd.DataFrame:
    """N×N symmetric weighted adjacency (cik-indexed, zero diagonal) from an edge list."""
    idx = [int(c) for c in ciks]
    A = pd.DataFrame(0.0, index=idx, columns=idx)
    for r in edges.itertuples(index=False):
        a, b = int(r.cik_a), int(r.cik_b)
        if a in A.index and b in A.columns:
            w = float(getattr(r, weight))
            A.at[a, b] = w
            A.at[b, a] = w
    return A


def degree(adj: pd.DataFrame, threshold: float = 0.0) -> pd.Series:
    """Unweighted degree: number of incident edges with weight > ``threshold`` (zero diagonal)."""
    mask = adj.to_numpy() > threshold
    np.fill_diagonal(mask, False)
    return pd.Series(mask.sum(axis=1), index=adj.index, name="degree")


def weighted_degree(adj: pd.DataFrame) -> pd.Series:
    """Strength: row-sum of edge weights (zero diagonal)."""
    A = adj.to_numpy(dtype=float).copy()
    np.fill_diagonal(A, 0.0)
    return pd.Series(A.sum(axis=1), index=adj.index, name="weighted_degree")


def eigenvector_centrality(adj: pd.DataFrame, *, max_iter: int = 1000, tol: float = 1e-9
                           ) -> pd.Series:
    """Dominant eigenvector of the symmetric adjacency via power iteration.

    Power-iterate on ``A + I`` (``x ← Mx; x ← x/||x||_2`` until ``||Δx||_∞ < tol``). The identity
    shift leaves the eigenvectors unchanged but makes the dominant eigenvalue unique and positive,
    so iteration converges even for bipartite graphs (a plain ``A`` has ±λ of equal magnitude and
    oscillates). The adjacency is non-negative (cosine ≥ 0), so the leading eigenvector is
    single-signed; we return it non-negative and rescaled so ``max == 1``. Returns zeros for
    0/1-node or edgeless graphs.
    """
    idx = adj.index
    n = len(idx)
    if n == 0:
        return pd.Series(dtype=float, name="eigenvector_cent")
    if n == 1:
        return pd.Series([0.0], index=idx, name="eigenvector_cent")

    A = adj.to_numpy(dtype=float).copy()
    np.fill_diagonal(A, 0.0)
    if not (A > 0).any():                                 # edgeless graph
        return pd.Series(np.zeros(n), index=idx, name="eigenvector_cent")

    M = A + np.eye(n)                                     # identity shift breaks the ±λ degeneracy
    x = np.full(n, 1.0 / np.sqrt(n))
    for _ in range(max_iter):
        y = M @ x
        y /= float(np.linalg.norm(y))
        if np.max(np.abs(y - x)) < tol:
            x = y
            break
        x = y
    x = np.abs(x)
    m = float(x.max())
    if m > 0.0:
        x = x / m
    return pd.Series(x, index=idx, name="eigenvector_cent")


def node_metrics(edges: pd.DataFrame, nodes_meta: pd.DataFrame, *,
                 weight: str = "cosine", threshold: float = 0.0) -> pd.DataFrame:
    """Append degree / weighted_degree / eigenvector_cent to a per-quarter node metadata frame."""
    ciks = list(nodes_meta["cik"])
    adj = adjacency(edges, ciks, weight=weight)
    deg, wdeg, eig = degree(adj, threshold), weighted_degree(adj), eigenvector_centrality(adj)
    out = nodes_meta.copy()
    out["degree"] = out["cik"].map(deg).fillna(0).astype(int)
    out["weighted_degree"] = out["cik"].map(wdeg).fillna(0.0)
    out["eigenvector_cent"] = out["cik"].map(eig).fillna(0.0)
    return out


def _name_map(*edge_frames: pd.DataFrame) -> dict[int, str]:
    names: dict[int, str] = {}
    for e in edge_frames:
        for col_c, col_n in (("cik_a", "manager_a"), ("cik_b", "manager_b")):
            if col_c in e.columns:
                names.update({int(c): n for c, n in zip(e[col_c], e[col_n])})
    return names


def relationship_deltas(store, metric: str = "cosine", lookback: int = 1, band: float = 0.05
                        ) -> pd.DataFrame:
    """Per-pair change in ``metric`` between the latest quarter and the one ``lookback`` before it.

    ``status`` ∈ {new, dropped, emerging, fading, stable}: ``new``/``dropped`` = the pair co-filed
    in only one of the two quarters; ``emerging``/``fading`` = |Δ| beyond ``band``; else ``stable``.
    Sorted by |Δ| descending — the headline movers first.
    """
    periods = store.periods()
    if len(periods) < lookback + 1:
        return pd.DataFrame(columns=_DELTA_COLS)
    prev_p, curr_p = periods[-1 - lookback], periods[-1]
    prev, curr = store.load_edges(prev_p), store.load_edges(curr_p)
    names = _name_map(prev, curr)

    def keyed(df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series(dtype=float)
        return df.set_index(["cik_a", "cik_b"])[metric]

    pi, ci = keyed(prev), keyed(curr)
    rows = []
    for pair in pi.index.union(ci.index):
        pv = float(pi[pair]) if pair in pi.index else np.nan
        cv = float(ci[pair]) if pair in ci.index else np.nan
        delta = (0.0 if np.isnan(cv) else cv) - (0.0 if np.isnan(pv) else pv)
        if np.isnan(pv):
            status = "new"
        elif np.isnan(cv):
            status = "dropped"
        elif delta > band:
            status = "emerging"
        elif delta < -band:
            status = "fading"
        else:
            status = "stable"
        a, b = int(pair[0]), int(pair[1])
        rows.append({"cik_a": a, "cik_b": b, "manager_a": names.get(a, str(a)),
                     "manager_b": names.get(b, str(b)), "prev": pv, "curr": cv,
                     "delta": delta, "status": status})
    df = pd.DataFrame(rows, columns=_DELTA_COLS)
    return df.reindex(df["delta"].abs().sort_values(ascending=False).index).reset_index(drop=True)
