"""Node metrics + relationship-evolution math (qhfi.ownership.metrics)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.ownership.graph import EDGE_COLS
from qhfi.ownership.metrics import (
    adjacency,
    degree,
    eigenvector_centrality,
    relationship_deltas,
    weighted_degree,
)


def _edges(triples: list[tuple[int, int, float]]) -> pd.DataFrame:
    """Edge frame from (cik_a, cik_b, cosine) triples."""
    rows = [{"period": "p", "cik_a": a, "cik_b": b, "manager_a": f"M{a}", "manager_b": f"M{b}",
             "cosine": w, "jaccard": w, "shared_n": 1, "shared_usd": 1.0, "filed": "2024-08-14"}
            for a, b, w in triples]
    return pd.DataFrame(rows, columns=EDGE_COLS)


# star: hub 1 connected to leaves 2,3,4; leaves not connected to each other
STAR = _edges([(1, 2, 0.9), (1, 3, 0.9), (1, 4, 0.9)])
CIKS = [1, 2, 3, 4]


def test_adjacency_square_symmetric_zero_diagonal():
    A = adjacency(STAR, CIKS)
    assert A.shape == (4, 4)
    assert np.allclose(A.to_numpy(), A.to_numpy().T)
    assert np.allclose(np.diag(A.to_numpy()), 0.0)


def test_degree_and_strength_by_hand():
    A = adjacency(STAR, CIKS)
    assert degree(A).to_dict() == {1: 3, 2: 1, 3: 1, 4: 1}
    wd = weighted_degree(A)
    assert wd[1] == pytest.approx(2.7)
    assert wd[2] == pytest.approx(0.9)


def test_eigenvector_centrality_hub_is_most_central():
    eig = eigenvector_centrality(adjacency(STAR, CIKS))
    assert (eig >= 0).all() and eig.max() == pytest.approx(1.0)
    assert eig[1] == pytest.approx(1.0)                  # hub scaled to 1
    assert all(eig[leaf] < eig[1] for leaf in (2, 3, 4))
    assert eig[2] == pytest.approx(eig[3]) == pytest.approx(eig[4])  # symmetric leaves


def test_eigenvector_centrality_edge_cases():
    assert eigenvector_centrality(adjacency(_edges([]), [])).empty       # 0 nodes
    one = eigenvector_centrality(adjacency(_edges([]), [1]))
    assert one.to_list() == [0.0]                                        # 1 node
    edgeless = eigenvector_centrality(adjacency(_edges([]), [1, 2, 3]))
    assert (edgeless == 0.0).all()                                       # no edges


class _DeltaStore:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._f = frames

    def periods(self) -> list[str]:
        return sorted(self._f)

    def load_edges(self, p: str) -> pd.DataFrame:
        return self._f[p]


def test_relationship_deltas_labels_transitions():
    prev = _edges([(1, 2, 0.2), (1, 3, 0.5)])
    curr = _edges([(1, 2, 0.8), (2, 3, 0.3)])   # (1,2) up, (1,3) gone, (2,3) appeared
    df = relationship_deltas(_DeltaStore({"2024-03-31": prev, "2024-06-30": curr}))
    status = {(r.cik_a, r.cik_b): r.status for r in df.itertuples()}
    assert status[(1, 2)] == "emerging"
    assert status[(1, 3)] == "dropped"
    assert status[(2, 3)] == "new"
    assert df["delta"].abs().is_monotonic_decreasing       # sorted by |delta| desc


def test_relationship_deltas_insufficient_history():
    one = _edges([(1, 2, 0.5)])
    assert relationship_deltas(_DeltaStore({"2024-06-30": one})).empty
