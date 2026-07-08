"""Tests for agent-workflow scenarios: the Scenario node, context overrides, market-shock
stress in the backtest node, persistence, and end-to-end context plumbing through arun_stream.

Offline tests use nodes that need no market data (scenario / data / output); the backtest-shock
test uses the dow30 lake and skips if it isn't cached.

Run: `cd backend && pytest tests/test_agent_scenarios.py -v`
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import agent_graph as ag
from app.services import agent_nodes as an
from app.services import scenario as sc
from app.store import TerminalStore


def _deps():
    return an.AgentDeps(dm=None, fstore=None, fprov=None, llm=None, model="stub", llm_base_url="")


def _drain(spec: dict, context: dict | None = None) -> list[dict]:
    async def run() -> list[dict]:
        return [f async for f in ag.arun_stream(spec, {}, _deps(), context)]

    return asyncio.run(run())


# ── scenario node + helpers ─────────────────────────────────────────────────────
def test_scenario_node_emits_variables_and_shocks():
    out = an.run_node(
        {"type": "scenario", "config": {"universe": "sp500", "factor": "value", "equity_pct": -10, "vol_mult": 1.5}},
        {}, _deps(), {},
    )
    v = out["value"]
    assert v["universe"] == "sp500" and v["factor"] == "value"
    assert v["shocks"]["equity_pct"] == -10.0 and v["shocks"]["vol_mult"] == 1.5


def test_stress_weights_math():
    # a 100%-long equity book under a -10% move loses 10%
    s = sc.stress_weights([{"symbol": "AAPL", "weight": 60}, {"symbol": "MSFT", "weight": 40}], {"equity_pct": -10, "crypto_pct": 0, "vol_mult": 1})
    assert s["stress_pnl_pct"] == pytest.approx(-10.0, abs=1e-6)
    # dollar-neutral book (net 0) is ~insensitive to a market-wide move
    s2 = sc.stress_weights([{"symbol": "A", "weight": 50}, {"symbol": "B", "weight": -50}], {"equity_pct": -10})
    assert s2["stress_pnl_pct"] == pytest.approx(0.0, abs=1e-6)


def test_is_active():
    assert not sc.is_active({"equity_pct": 0, "crypto_pct": 0, "vol_mult": 1})
    assert sc.is_active({"equity_pct": -5, "crypto_pct": 0, "vol_mult": 1})
    assert sc.is_active({"equity_pct": 0, "crypto_pct": 0, "vol_mult": 2})


# ── context overrides ───────────────────────────────────────────────────────────
def test_context_overrides_node_config():
    data = an.run_node({"type": "data", "config": {"universe": "dow30"}}, {}, _deps(), {"universe": "sp500"})
    assert data["value"]["universe"] == "sp500"
    strat = an.run_node({"type": "strategy", "config": {"factor": "momentum", "mode": "long_only"}}, {}, _deps(),
                        {"factor": "value", "mode": "long_short"})
    assert strat["value"]["factor"] == "value" and strat["value"]["mode"] == "long_short"


def test_no_context_keeps_node_config():
    data = an.run_node({"type": "data", "config": {"universe": "nasdaq100"}}, {}, _deps(), {})
    assert data["value"]["universe"] == "nasdaq100"


# ── persistence + build_context ─────────────────────────────────────────────────
def test_scenario_crud(tmp_path):
    store = TerminalStore(tmp_path / "s.sqlite")
    store.init()
    sc.save_scenario(store, "S1", {"variables": {"universe": "sp500", "factor": "value", "junk": "x"}, "shocks": {"equity_pct": -8}})
    lst = sc.list_scenarios(store)["scenarios"]
    assert len(lst) == 1
    rec = lst[0]
    assert rec["name"] == "S1"
    assert rec["variables"] == {"universe": "sp500", "factor": "value"}  # unknown key dropped
    assert rec["shocks"]["equity_pct"] == -8.0 and rec["shocks"]["vol_mult"] == 1.0  # defaults filled
    sc.remove_scenario(store, "S1")
    assert sc.list_scenarios(store)["scenarios"] == []


def test_build_context_loads_named_scenario(tmp_path):
    store = TerminalStore(tmp_path / "s.sqlite")
    store.init()
    sc.save_scenario(store, "Risk", {"variables": {"universe": "sp500"}, "shocks": {"equity_pct": -10}})
    ctx = sc.build_context(store, scenario="Risk", context=None, seed={"symbol": "AAPL"})
    assert ctx["symbol"] == "AAPL" and ctx["universe"] == "sp500"
    assert ctx["shocks"]["equity_pct"] == -10.0


# ── end-to-end through the executor (offline) ────────────────────────────────────
def test_arun_stream_scenario_node_flows_downstream():
    spec = {
        "nodes": [
            {"id": "n0", "type": "scenario", "x": 0, "y": 0, "config": {"universe": "sp500", "factor": "value"}},
            {"id": "n1", "type": "data", "x": 1, "y": 0, "config": {}},
            {"id": "n2", "type": "output", "x": 2, "y": 0, "config": {}},
        ],
        "edges": [{"source": "n0", "target": "n1"}, {"source": "n1", "target": "n2"}],
    }
    frames = _drain(spec)
    done = [f for f in frames if f["type"] == "node" and f["status"] == "done"]
    data_frame = next(f for f in done if f["id"] == "n1")
    assert data_frame["value"]["universe"] == "sp500"  # scenario node fed the data node
    assert frames[-1]["type"] == "done"


def test_arun_stream_manager_context_overrides():
    spec = {
        "nodes": [
            {"id": "n1", "type": "data", "x": 0, "y": 0, "config": {"universe": "dow30"}},
            {"id": "n2", "type": "output", "x": 1, "y": 0, "config": {}},
        ],
        "edges": [{"source": "n1", "target": "n2"}],
    }
    frames = _drain(spec, context={"universe": "nasdaq100", "shocks": sc.default_shocks()})
    data_frame = next(f for f in frames if f["type"] == "node" and f["status"] == "done" and f["id"] == "n1")
    assert data_frame["value"]["universe"] == "nasdaq100"


def test_arun_stream_fanin_node_runs_once():
    # A fan-in node (multiple incoming edges) must run EXACTLY once — after ALL its predecessors —
    # not once per incoming edge. Mirrors the Committee Review demo shape (n2 has inputs from both
    # n0 and n1). Regression for the langgraph join fix in agent_graph.build(): grouping incoming
    # edges into add_edge([sources], target) instead of adding each edge separately (which fired the
    # node, and everything downstream, once per predecessor).
    spec = {
        "nodes": [
            {"id": "n0", "type": "scenario", "x": 0, "y": 0, "config": {"universe": "sp500", "factor": "value"}},
            {"id": "n1", "type": "data", "x": 1, "y": 0, "config": {}},
            {"id": "n2", "type": "strategy", "x": 2, "y": 0, "config": {}},  # fan-in: from n0 AND n1
            {"id": "n3", "type": "output", "x": 3, "y": 0, "config": {}},
        ],
        "edges": [
            {"source": "n0", "target": "n1"},
            {"source": "n0", "target": "n2"},
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }
    frames = _drain(spec)
    assert frames[-1]["type"] == "done"
    for nid in ("n0", "n1", "n2", "n3"):
        running = [f for f in frames if f["type"] == "node" and f["id"] == nid and f["status"] == "running"]
        done = [f for f in frames if f["type"] == "node" and f["id"] == nid and f["status"] == "done"]
        assert len(running) == 1, f"{nid} emitted {len(running)} running frames (expected 1 — fan-in double-run)"
        assert len(done) == 1, f"{nid} emitted {len(done)} done frames (expected 1 — fan-in double-run)"


# ── backtest shock (needs data) ──────────────────────────────────────────────────
def test_backtest_node_applies_shock(providers_or_skip):
    deps = providers_or_skip
    node = {"type": "backtest", "config": {"universe": "dow30", "factor": "momentum", "mode": "long_only", "years": 3}}
    try:
        out = an.run_node(node, {}, deps, {"shocks": {"equity_pct": -10, "crypto_pct": 0, "vol_mult": 1.5}})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"dow30 not cached: {e}")
    assert "stress" in out["value"], out["value"].keys()
    assert out["value"]["stress"]["equity_pct"] == -10
    assert "Stress" in out["summary"]


@pytest.fixture
def providers_or_skip():
    from app import deps as appdeps
    return an.AgentDeps(
        dm=appdeps.get_data_manager(), fstore=appdeps.get_fundamentals_store(),
        fprov=appdeps.get_fundamentals_provider(), llm=None, model="stub", llm_base_url="",
    )
