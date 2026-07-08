"""Builders — 13F holdings → a per-quarter manager relationship graph.

For one ``period_of_report`` the node set is the managers who filed for that quarter (from the
``HoldingsStore`` catalog); a manager absent that quarter is simply not a node — no zero-vector
imputation, since cosine of a zero vector is undefined. Edges are the holdings-overlap metrics
(``pair_edge``) for every unordered pair of co-filers. The PIT anchor on an edge is the later of
the two managers' filing dates — the date the relationship became knowable — so the derived graph
stays backtest-safe like its parent ``institutional_holdings_13f`` dataset.

Edge math is here (pandas/numpy only); node-level graph metrics live in ``metrics.py``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.ownership.metrics import node_metrics

EDGE_COLS = ["period", "cik_a", "cik_b", "manager_a", "manager_b",
             "cosine", "jaccard", "shared_n", "shared_usd", "filed"]
NODE_COLS = ["period", "cik", "manager", "filed", "degree", "weighted_degree",
             "eigenvector_cent", "n_positions", "value_usd_bn"]


def value_vector(holdings: pd.DataFrame) -> pd.Series:
    """CUSIP → total ``value_usd`` for one manager-quarter (summed across share classes, >0 only)."""
    if holdings.empty or "value_usd" not in holdings.columns:
        return pd.Series(dtype=float, name="value_usd")
    v = holdings.groupby("cusip")["value_usd"].sum()
    return v[v > 0].rename("value_usd")


def weight_vector(holdings: pd.DataFrame) -> pd.Series:
    """``value_vector`` normalized to sum to 1 (the portfolio weights over held CUSIPs)."""
    v = value_vector(holdings)
    total = float(v.sum())
    return v / total if total > 0 else v


def _profile(holdings: pd.DataFrame) -> dict:
    """Precompute the per-manager quantities every pairwise edge needs (so they are computed once
    per manager, not once per pair — the O(n²) hot path at 100 nodes)."""
    v = value_vector(holdings)
    total = float(v.sum())
    w = v / total if total > 0 else v
    return {"v": v, "w": w, "norm": float(np.sqrt((w ** 2).sum())), "cusips": set(v.index)}


def _edge_metrics(a: dict, b: dict) -> dict:
    """Holdings-overlap metrics from two precomputed manager profiles (see ``_profile``)."""
    inter = a["cusips"] & b["cusips"]
    union = len(a["cusips"]) + len(b["cusips"]) - len(inter)
    if a["norm"] == 0.0 or b["norm"] == 0.0:
        cosine = 0.0
    else:                                   # dot over the intersection (absent terms are 0)
        common = a["w"].index.intersection(b["w"].index)
        dot = float(np.dot(a["w"].reindex(common).to_numpy(), b["w"].reindex(common).to_numpy()))
        cosine = max(0.0, min(1.0, dot / (a["norm"] * b["norm"])))
    if inter:
        idx = list(inter)
        shared_usd = float(np.minimum(a["v"].reindex(idx).to_numpy(),
                                      b["v"].reindex(idx).to_numpy()).sum())
    else:
        shared_usd = 0.0
    return {"cosine": cosine, "jaccard": len(inter) / union if union else 0.0,
            "shared_n": len(inter), "shared_usd": shared_usd}


def pair_edge(a_h: pd.DataFrame, b_h: pd.DataFrame) -> dict:
    """Holdings-overlap metrics between two managers' quarter holdings.

    * ``cosine``      — cosine similarity of value-weight vectors over the union CUSIP space
                        (absent CUSIP = 0 weight). Range [0, 1]; scale-free; the PRIMARY edge weight.
    * ``jaccard``     — |S_a ∩ S_b| / |S_a ∪ S_b| over held-CUSIP sets (ignores sizing).
    * ``shared_n``    — count of co-held CUSIPs.
    * ``shared_usd``  — Σ min(value_usd_a, value_usd_b) over co-held CUSIPs (common exposure).
    """
    return _edge_metrics(_profile(a_h), _profile(b_h))


def build_snapshot(holdings_store, period: str, *, catalog: pd.DataFrame | None = None
                   ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the (edges, nodes) frames for one quarter from a ``HoldingsStore``.

    ``catalog`` may be passed to avoid recomputing ``holdings_store.catalog()`` per period.
    Returns well-formed (possibly empty) frames with the canonical ``EDGE_COLS`` / ``NODE_COLS``.
    """
    cat = catalog if catalog is not None else holdings_store.catalog()
    rows = cat[cat["period"] == period] if not cat.empty else cat

    prof: dict[int, dict] = {}
    meta: dict[int, dict] = {}
    for r in rows.itertuples(index=False):
        cik = int(r.cik)
        p = _profile(holdings_store.load(cik, period))
        prof[cik] = p
        meta[cik] = {"manager": r.manager, "filed": r.filed,
                     "n_positions": int(len(p["v"])), "value_usd_bn": round(float(p["v"].sum()) / 1e9, 4)}

    ciks = sorted(prof)
    edge_rows = []
    for i, a in enumerate(ciks):
        for b in ciks[i + 1:]:
            edge_rows.append({"period": period, "cik_a": a, "cik_b": b,
                              "manager_a": meta[a]["manager"], "manager_b": meta[b]["manager"],
                              **_edge_metrics(prof[a], prof[b]),
                              "filed": max(meta[a]["filed"], meta[b]["filed"])})
    edges = pd.DataFrame(edge_rows, columns=EDGE_COLS)

    nodes_meta = pd.DataFrame(
        [{"period": period, "cik": c, "manager": meta[c]["manager"], "filed": meta[c]["filed"],
          "n_positions": meta[c]["n_positions"], "value_usd_bn": meta[c]["value_usd_bn"]}
         for c in ciks],
        columns=["period", "cik", "manager", "filed", "n_positions", "value_usd_bn"])
    nodes = node_metrics(edges, nodes_meta)[NODE_COLS]
    return edges, nodes


def build_all(holdings_store, graph_store, *, ciks: set[int] | None = None) -> list[str]:
    """Build + persist a snapshot for every quarter present in the holdings lake. Returns the
    list of periods written (sorted).

    ``ciks`` restricts the node set to a fixed roster (e.g. a top-100 manager universe), so other
    managers already in the lake don't leak into the graph.
    """
    cat = holdings_store.catalog()
    if cat.empty:
        return []
    if ciks is not None:
        cat = cat[cat["cik"].isin(ciks)]
    written = []
    for period in sorted(cat["period"].unique()):
        edges, nodes = build_snapshot(holdings_store, period, catalog=cat)
        graph_store.save_edges(period, edges)
        graph_store.save_nodes(period, nodes)
        written.append(period)
    return written
