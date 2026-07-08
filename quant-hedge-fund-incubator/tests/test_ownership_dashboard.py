"""Self-contained HTML dashboard generation (qhfi.ownership.dashboard)."""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from qhfi.data.holdings import HoldingsStore
from qhfi.ownership.dashboard import dashboard_data, write_dashboard
from qhfi.ownership.graph import EDGE_COLS, NODE_COLS
from qhfi.ownership.store import ManagerGraphStore


def _seed(tmp_path) -> ManagerGraphStore:
    store = ManagerGraphStore(tmp_path)
    for period in ("2024-03-31", "2024-06-30"):
        edges = pd.DataFrame([
            {"period": period, "cik_a": 1, "cik_b": 2, "manager_a": "Alpha", "manager_b": "Beta",
             "cosine": 0.7, "jaccard": 0.5, "shared_n": 3, "shared_usd": 1.0, "filed": "x"},
        ], columns=EDGE_COLS)
        nodes = pd.DataFrame([
            {"period": period, "cik": c, "manager": m, "filed": "x", "degree": 1,
             "weighted_degree": 0.7, "eigenvector_cent": e, "n_positions": 5, "value_usd_bn": 2.0}
            for c, m, e in [(1, "Alpha", 1.0), (2, "Beta", 0.8)]
        ], columns=NODE_COLS)
        store.save_edges(period, edges)
        store.save_nodes(period, nodes)
    return store


def test_dashboard_data_keyed_by_period(tmp_path):
    data = dashboard_data(_seed(tmp_path))
    assert set(data) == {"2024-03-31", "2024-06-30"}
    assert len(data["2024-06-30"]["nodes"]) == 2
    assert len(data["2024-06-30"]["links"]) == 1


def test_write_dashboard_embeds_holdings(tmp_path):
    store = _seed(tmp_path)
    hs = HoldingsStore(tmp_path)
    holdings = pd.DataFrame({"issuer": ["APPLE INC", "MICROSOFT CORP"],
                             "cusip": ["c1", "c2"], "value_usd": [7e8, 3e8]})
    hs.save(1, "Alpha", "2024-06-30", holdings)
    html = write_dashboard(store, tmp_path / "d.html", holdings_store=hs).read_text(encoding="utf-8")
    data = json.loads(re.search(r"const HOLDINGS = (\{.*?\});\nconst qEl", html, re.S).group(1))
    top = data["2024-06-30"]["1"]
    assert top[0]["issuer"] == "APPLE INC"                       # sorted by value desc
    assert top[0]["weight"] == pytest.approx(0.7)                # 7e8 / (7e8+3e8)
    assert "APPLE INC" in html


def test_write_dashboard_self_contained(tmp_path):
    out = write_dashboard(_seed(tmp_path), tmp_path / "dash.html", metric="cosine")
    html = out.read_text(encoding="utf-8")
    # no template placeholder leaks
    for token in ("__DATA__", "__PERIODS__", "__METRIC__", "__TITLE__"):
        assert token not in html
    # embedded data is valid JSON and round-trips
    data = json.loads(re.search(r"const DATA = (\{.*?\});\nconst PERIODS", html, re.S).group(1))
    assert data["2024-06-30"]["nodes"][0]["manager"] in ("Alpha", "Beta")
    assert "3d-force-graph" in html and "ForceGraph3D" in html   # 3D renderer referenced
