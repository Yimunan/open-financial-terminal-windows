"""Edge math + snapshot assembly for the 13F manager graph (qhfi.ownership.graph)."""

from __future__ import annotations

import pandas as pd
import pytest

from qhfi.ownership.graph import EDGE_COLS, NODE_COLS, build_snapshot, pair_edge


def _h(values: dict[str, float]) -> pd.DataFrame:
    """Minimal holdings frame: cusip → value_usd."""
    return pd.DataFrame({"cusip": list(values), "value_usd": list(values.values())})


class _FakeHoldings:
    """Stand-in for HoldingsStore: holds {(cik, period): frame} + per-(cik,period) metadata."""

    def __init__(self, rows: list[dict]):
        self._rows = rows  # each: cik, manager, period, filed, frame

    def catalog(self) -> pd.DataFrame:
        return pd.DataFrame([{"manager": r["manager"], "cik": r["cik"], "period": r["period"],
                              "filed": r["filed"]} for r in self._rows])

    def load(self, cik: int, period: str) -> pd.DataFrame:
        return next(r["frame"] for r in self._rows if r["cik"] == cik and r["period"] == period)


def test_cosine_identical_is_one_and_symmetric():
    h = _h({"A": 100, "B": 100})
    e = pair_edge(h, h.copy())
    assert e["cosine"] == pytest.approx(1.0)
    assert pair_edge(h, h.copy()) == pair_edge(h.copy(), h)   # symmetry


def test_cosine_disjoint_is_zero():
    e = pair_edge(_h({"A": 100}), _h({"B": 100}))
    assert e["cosine"] == 0.0
    assert e["jaccard"] == 0.0
    assert e["shared_n"] == 0
    assert e["shared_usd"] == 0.0


def test_jaccard_and_shared_metrics_by_hand():
    a = _h({"X": 100, "Y": 50, "Z": 10})
    b = _h({"Y": 200, "Z": 30, "W": 5})
    e = pair_edge(a, b)
    assert e["jaccard"] == pytest.approx(2 / 4)              # {Y,Z} shared of {X,Y,Z,W}
    assert e["shared_n"] == 2
    assert e["shared_usd"] == pytest.approx(min(50, 200) + min(10, 30))  # 50 + 10


def test_pair_edge_symmetry_general():
    a, b = _h({"X": 70, "Y": 30}), _h({"X": 10, "Z": 90})
    assert pair_edge(a, b) == pair_edge(b, a)


def test_build_snapshot_two_filers_one_edge():
    store = _FakeHoldings([
        {"cik": 1, "manager": "Alpha", "period": "2024-06-30", "filed": "2024-08-10",
         "frame": _h({"X": 100, "Y": 100})},
        {"cik": 2, "manager": "Beta", "period": "2024-06-30", "filed": "2024-08-14",
         "frame": _h({"X": 100, "Y": 100})},
    ])
    edges, nodes = build_snapshot(store, "2024-06-30")
    assert list(edges.columns) == EDGE_COLS and list(nodes.columns) == NODE_COLS
    assert len(edges) == 1 and len(nodes) == 2
    assert edges["cosine"].iloc[0] == pytest.approx(1.0)
    assert edges["filed"].iloc[0] == "2024-08-14"           # PIT anchor = later filing
    assert edges["cik_a"].iloc[0] < edges["cik_b"].iloc[0]   # canonical ordering


def test_build_snapshot_single_filer_no_edges_no_crash():
    store = _FakeHoldings([
        {"cik": 1, "manager": "Alpha", "period": "2024-03-31", "filed": "2024-05-10",
         "frame": _h({"X": 100})},
    ])
    edges, nodes = build_snapshot(store, "2024-03-31")
    assert edges.empty and list(edges.columns) == EDGE_COLS
    assert len(nodes) == 1
    assert nodes["degree"].iloc[0] == 0
    assert nodes["eigenvector_cent"].iloc[0] == 0.0
