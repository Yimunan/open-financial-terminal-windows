"""Tests for the ReAct workflow-coder's mutating-tool state machine
(services.agent_coder._apply + _next_id).

Hermetic: _apply and _next_id are pure dict mutators — they operate on an in-memory
spec ({"nodes": [...], "edges": [...]}) and only reach out to two sibling validators that
are themselves pure (agent_code._validate for code-step Python, agent_graph._validate for
DAG/cycle checks). So there is nothing to stub: every spec is hand-built with REAL node-type
keys from agent_nodes._BY_KEY, and the assertions pin the (observation, mutated) contract of
each tool branch — add_node / edit_config / connect / disconnect / delete_node — plus the id
allocator. The streaming arun_coder loop (async + real LangGraph) is out of scope.

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_agent_coder.py -q`
"""

from __future__ import annotations

import pytest

from app.services import agent_coder as ac
from app.services.agent_nodes import _BY_KEY


# ── fixtures / helpers ─────────────────────────────────────────────────────────────
def _spec(nodes=None, edges=None) -> dict:
    """A fresh spec dict. _apply mutates it in place, so build a new one per test."""
    return {"nodes": list(nodes or []), "edges": list(edges or [])}


def _node(nid: str, typ: str = "input", **config) -> dict:
    return {"id": nid, "type": typ, "x": 0, "y": 0, "config": dict(config)}


def _ids(spec: dict) -> set[str]:
    return {n["id"] for n in spec["nodes"]}


def _edge_set(spec: dict) -> set[tuple[str, str]]:
    return {(e["source"], e["target"]) for e in spec["edges"]}


# ── _next_id ───────────────────────────────────────────────────────────────────────
def test_next_id_increments_past_max():
    # empty -> n1
    assert ac._next_id(_spec()) == "n1"
    # max over existing n-digit ids, gaps ignored ([n1, n3] -> n4)
    spec = _spec([_node("n1"), _node("n3")])
    assert ac._next_id(spec) == "n4"
    # non-n ids are skipped entirely (only "n<digits>" counts)
    spec = _spec([_node("foo"), _node("n2"), _node("bar")])
    assert ac._next_id(spec) == "n3"


# ── add_node ─────────────────────────────────────────────────────────────────────
def test_apply_add_node_fills_defaults_and_assigns_id():
    spec = _spec([_node("n1", "input")])
    obs, mutated = ac._apply("add_node", {"type": "strategy"}, spec)

    assert mutated is True
    assert "n2" in obs and "strategy" in obs
    added = spec["nodes"][-1]
    assert added["id"] == "n2"
    assert added["type"] == "strategy"
    # every param default from _BY_KEY["strategy"] is filled into config
    expected = {p["key"]: p["default"] for p in _BY_KEY["strategy"]["params"]}
    for k, v in expected.items():
        assert added["config"][k] == v


def test_apply_add_unknown_type_is_rejected():
    spec = _spec([_node("n1", "input")])
    obs, mutated = ac._apply("add_node", {"type": "definitely_not_a_node"}, spec)

    assert mutated is False
    assert "unknown node type" in obs
    # spec is untouched
    assert _ids(spec) == {"n1"}


def test_apply_add_code_node_with_bad_python_warns_but_adds():
    spec = _spec()
    # an import is rejected by agent_code._validate -> the node still adds, with a warning note
    obs, mutated = ac._apply(
        "add_node", {"type": "code", "config": {"code": "import os\nresult = 1"}}, spec
    )

    assert mutated is True  # warning does NOT block the add
    assert "warning" in obs.lower()
    assert "imports are not allowed" in obs
    added = spec["nodes"][-1]
    assert added["type"] == "code"
    assert added["config"]["code"] == "import os\nresult = 1"


def test_apply_add_code_node_valid_python_no_warning():
    # the default STARTER_CODE is valid -> clean observation, no "(warning: ...)"
    spec = _spec()
    obs, mutated = ac._apply("add_node", {"type": "code"}, spec)
    assert mutated is True
    assert "warning" not in obs.lower()


# ── edit_config ──────────────────────────────────────────────────────────────────
def test_apply_edit_config_missing_node():
    spec = _spec([_node("n1", "input")])
    obs, mutated = ac._apply(
        "edit_config", {"id": "n9", "key": "symbol", "value": "TSLA"}, spec
    )
    assert mutated is False
    assert "no node 'n9'" in obs


def test_apply_edit_config_sets_key():
    spec = _spec([_node("n1", "input", symbol="AAPL")])
    obs, mutated = ac._apply(
        "edit_config", {"id": "n1", "key": "symbol", "value": "TSLA"}, spec
    )
    assert mutated is True
    assert spec["nodes"][0]["config"]["symbol"] == "TSLA"
    assert "set n1.symbol" in obs


def test_apply_edit_config_bad_code_warns_but_mutates():
    spec = _spec([_node("n1", "code", code="result = 1")])
    obs, mutated = ac._apply(
        "edit_config", {"id": "n1", "key": "code", "value": "import sys\nresult = 2"}, spec
    )
    # a bad-Python code key returns a warning observation but STILL mutates
    assert mutated is True
    assert "warning" in obs.lower()
    assert spec["nodes"][0]["config"]["code"] == "import sys\nresult = 2"


# ── connect / disconnect ──────────────────────────────────────────────────────────
def test_apply_connect_rejects_cycle_and_duplicate():
    # n1 -> n2 exists; connecting n2 -> n1 would form a cycle (rejected via ag._validate)
    spec = _spec(
        [_node("n1", "data"), _node("n2", "strategy")],
        [{"source": "n1", "target": "n2"}],
    )

    # duplicate edge -> rejected, not mutated
    obs, mutated = ac._apply("connect", {"source": "n1", "target": "n2"}, spec)
    assert mutated is False
    assert "already exists" in obs
    assert _edge_set(spec) == {("n1", "n2")}

    # cycle -> rejected by the agent-graph validator (Kahn's algorithm)
    obs, mutated = ac._apply("connect", {"source": "n2", "target": "n1"}, spec)
    assert mutated is False
    assert "cannot connect" in obs and "cycle" in obs
    assert _edge_set(spec) == {("n1", "n2")}  # cycle edge NOT added


def test_apply_connect_missing_endpoint_rejected():
    spec = _spec([_node("n1", "data")])
    obs, mutated = ac._apply("connect", {"source": "n1", "target": "n9"}, spec)
    assert mutated is False
    assert "missing node" in obs
    assert spec["edges"] == []


def test_apply_connect_and_disconnect_roundtrip():
    spec = _spec([_node("n1", "data"), _node("n2", "strategy")])

    obs, mutated = ac._apply("connect", {"source": "n1", "target": "n2"}, spec)
    assert mutated is True
    assert "connected n1->n2" in obs
    assert _edge_set(spec) == {("n1", "n2")}

    obs, mutated = ac._apply("disconnect", {"source": "n1", "target": "n2"}, spec)
    assert mutated is True
    assert "disconnected n1->n2" in obs
    assert _edge_set(spec) == set()


# ── delete_node ──────────────────────────────────────────────────────────────────
def test_apply_delete_node_drops_incident_edges():
    # n1 -> n2 -> n3; deleting n2 must remove BOTH incident edges
    spec = _spec(
        [_node("n1", "data"), _node("n2", "strategy"), _node("n3", "portfolio")],
        [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}],
    )
    obs, mutated = ac._apply("delete_node", {"id": "n2"}, spec)

    assert mutated is True
    assert "deleted n2" in obs
    assert _ids(spec) == {"n1", "n3"}
    assert _edge_set(spec) == set()  # both edges touching n2 dropped


def test_apply_delete_node_missing_rejected():
    spec = _spec([_node("n1", "input")])
    obs, mutated = ac._apply("delete_node", {"id": "n9"}, spec)
    assert mutated is False
    assert "no node 'n9'" in obs
    assert _ids(spec) == {"n1"}
