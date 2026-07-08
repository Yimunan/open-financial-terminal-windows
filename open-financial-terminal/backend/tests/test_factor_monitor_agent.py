"""Tests for the chat-driven factor-performance agent.

The ReAct loop (`arun_factor_monitor_agent`), its tool dispatch, arg validation/clamping, and the
WebSocket route (`/api/factor-monitor/agent`). The LLM is always stubbed (scripted JSON actions).
The loop tests monkeypatch the data calls (`factor_monitor.scorecard` etc.) so they run without the
local data lake; `save`/`delete` use a real temp `TerminalStore`. One integration test drives the
real `fm.scorecard` and skips when dow30 is uncached.

Run: `cd backend && pytest tests/test_factor_monitor_agent.py -v`
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import deps as appdeps
from app.services import factor_monitor as fm
from app.services import factor_monitor_agent as fma
from app.store import TerminalStore


class StubLLM:
    """Returns scripted actions in order. A dict entry is JSON-encoded; a str entry is returned
    verbatim (to feed malformed / non-JSON replies)."""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[str] = []

    def complete(self, system: str, user: str, model=None, temperature: float = 0.0) -> str:
        self.calls.append(user)
        item = self.script.pop(0)
        return item if isinstance(item, str) else json.dumps(item)


@pytest.fixture(scope="module")
def providers():
    return (
        appdeps.get_data_manager(),
        appdeps.get_fundamentals_store(),
        appdeps.get_fundamentals_provider(),
    )


@pytest.fixture
def store(tmp_path):
    s = TerminalStore(tmp_path / "oft.sqlite")
    s.init()
    return s


def mk_deps(script: list, store=None, providers=(None, None, None)) -> fma.FMDeps:
    dm, fstore, fprov = providers
    return fma.FMDeps(dm=dm, fstore=fstore, fprov=fprov, llm=StubLLM(script), model="stub", store=store)


def drain(goal: str, context: dict, deps: fma.FMDeps) -> list[dict]:
    async def _run() -> list[dict]:
        return [f async for f in fma.arun_factor_monitor_agent(goal, context, deps)]

    return asyncio.run(_run())


# ── deterministic fakes for the data calls ───────────────────────────────────────────
def _fake_board(*a, **k) -> dict:
    # agent calls fm.scorecard(dm, fstore, fprov, universe, factors, horizon, q)
    universe = a[3] if len(a) > 3 else "dow30"
    horizon = a[5] if len(a) > 5 else 5
    q = a[6] if len(a) > 6 else 5
    return {
        "universe": universe, "horizon": horizon, "q": q, "n_instruments": 30,
        "window_start": "2024-01-01", "window_end": "2025-01-01",
        "rows": [
            {"factor": "momentum", "label": "Momentum", "ic_ir": 1.2, "mean_ic": 0.03},
            {"factor": "value", "label": "Value", "ic_ir": 0.4, "mean_ic": 0.01},
        ],
        "errors": [],
    }


def _fake_detail(*a, **k) -> dict:
    factor = a[4] if len(a) > 4 else "momentum"
    return {"factor": factor, "metrics": {"mean_ic": 0.03, "ic_ir": 1.2, "hit_rate": 55}}


def _fake_history(*a, **k) -> dict:
    name = a[1] if len(a) > 1 else "M"
    return {"monitor": name, "n_snapshots": 3, "factors": {"momentum": {"label": "Momentum", "mean_ic": [], "ic_ir": []}}}


def _fake_matrix(*a, **k) -> dict:
    universe = a[3] if len(a) > 3 else "dow30"
    return {
        "universe": universe, "method": "spearman", "n_instruments": 30,
        "window_start": "2024-01-01", "window_end": "2025-01-01",
        "factors": ["momentum", "value"], "labels": ["Momentum", "Value"],
        "matrix": [[1.0, 0.2], [0.2, 1.0]], "errors": [],
    }


def _patch_data(monkeypatch, *, scorecard=_fake_board, detail=_fake_detail,
                run_monitor=None, history=_fake_history, matrix=_fake_matrix) -> None:
    monkeypatch.setattr(fma.fm, "scorecard", scorecard)
    monkeypatch.setattr(fma.fm, "factor_detail", detail)
    monkeypatch.setattr(fma.fm, "monitor_history", history)
    monkeypatch.setattr(fma.fm, "correlation_matrix", matrix)
    monkeypatch.setattr(fma.fm, "run_monitor", run_monitor or (lambda *a, **k: {**_fake_board(), "monitor": a[4] if len(a) > 4 else "M"}))


# ── loop / dispatch ──────────────────────────────────────────────────────────────────
def test_rank_streams_thought_result_done(monkeypatch):
    _patch_data(monkeypatch)
    deps = mk_deps([
        {"thought": "ranking", "tool": "rank", "args": {"universe": "dow30", "horizon": 5, "q": 5}},
        {"done": True, "message": "ranked", "best_id": "r1"},
    ])
    frames = drain("rank the factors on the dow", {}, deps)

    assert [f["type"] for f in frames] == ["thought", "result", "done"], frames
    res = frames[1]
    assert res["kind"] == "board" and res["id"] == "r1"
    assert "Leaderboard" in res["label"]
    assert frames[-1]["best_id"] == "r1" and frames[-1]["message"] == "ranked"


def test_multistep_rank_then_drill(monkeypatch):
    _patch_data(monkeypatch)
    deps = mk_deps([
        {"tool": "rank", "args": {"universe": "dow30"}},
        {"tool": "drill", "args": {"factor": "momentum", "universe": "dow30"}},
        {"done": True, "message": "best is momentum", "best_id": "r2"},
    ])
    frames = drain("rank then drill the best", {}, deps)
    results = [f for f in frames if f["type"] == "result"]

    assert [r["kind"] for r in results] == ["board", "detail"], frames
    assert [r["id"] for r in results] == ["r1", "r2"]
    assert results[1]["data"]["factor"] == "momentum"
    assert frames[-1]["best_id"] == "r2"


def test_drill_clamps_invalid_args(monkeypatch):
    calls: list = []

    def rec_detail(*a, **k):
        calls.append((a, k))
        return _fake_detail(*a, **k)

    _patch_data(monkeypatch, detail=rec_detail)
    deps = mk_deps([
        {"tool": "drill", "args": {"factor": "bogus", "universe": "NOPE", "horizon": 999, "q": 1, "roll_window": 9999}},
        {"done": True, "message": "ok", "best_id": "r1"},
    ])
    frames = drain("drill nonsense", {}, deps)

    # assert via the result params the agent computed (q=1 clamps UP to the low bound 3) ...
    p = next(f for f in frames if f["type"] == "result")["params"]
    assert p == {"factor": "momentum", "universe": "dow30", "horizon": 63, "q": 3, "roll_window": 252}
    # ...and via the args actually passed to fm.factor_detail (dm,fstore,fprov,universe,factor,horizon,q + roll_window kw)
    a, k = calls[0]
    assert a[3] == "dow30" and a[4] == "momentum" and a[5] == 63 and a[6] == 3
    assert k["roll_window"] == 252


def test_rank_clamps_universe_and_ranges(monkeypatch):
    _patch_data(monkeypatch)
    deps = mk_deps([
        {"tool": "rank", "args": {"universe": "NOPE", "horizon": 0, "q": 99}},
        {"done": True, "message": "ok", "best_id": "r1"},
    ])
    frames = drain("rank nonsense", {}, deps)
    p = next(f for f in frames if f["type"] == "result")["params"]
    assert p["universe"] == "dow30"
    assert 1 <= p["horizon"] <= 63
    assert 3 <= p["q"] <= 10


def test_save_persists_and_emits_obs(store):
    deps = mk_deps([
        {"tool": "save", "args": {"name": "My Monitor", "universe": "dow30", "horizon": 5}},
        {"done": True, "message": "saved", "best_id": None},
    ], store=store)
    frames = drain("save this as My Monitor", {}, deps)

    obs = [f for f in frames if f["type"] == "obs"]
    assert obs and obs[0]["ok"] is True and "saved monitor" in obs[0]["text"]
    assert any(m["name"] == "My Monitor" for m in fm.list_monitors(store)["monitors"])


def test_delete_removes_monitor(store):
    fm.save_monitor(store, "Doomed", {"universe": "dow30", "horizon": 5})
    deps = mk_deps([
        {"tool": "delete", "args": {"name": "Doomed"}},
        {"done": True, "message": "gone", "best_id": None},
    ], store=store)
    frames = drain("delete Doomed", {}, deps)

    obs = [f for f in frames if f["type"] == "obs"]
    assert obs and obs[0]["ok"] is True
    assert fm.list_monitors(store)["monitors"] == []


def test_save_without_name_emits_failure_obs(store):
    deps = mk_deps([
        {"tool": "save", "args": {"universe": "dow30"}},
        {"done": True, "message": "done", "best_id": None},
    ], store=store)
    frames = drain("save it", {}, deps)

    obs = [f for f in frames if f["type"] == "obs"]
    assert obs and obs[0]["ok"] is False and "name is required" in obs[0]["text"]
    assert frames[-1]["type"] == "done"


def test_run_monitor_streams_board(monkeypatch, store):
    _patch_data(monkeypatch)
    deps = mk_deps([
        {"tool": "run", "args": {"name": "Dow"}},
        {"done": True, "message": "ran", "best_id": "r1"},
    ], store=store)
    frames = drain("run monitor Dow", {}, deps)
    res = next(f for f in frames if f["type"] == "result")
    assert res["kind"] == "board" and "Monitor ·" in res["label"]


def test_heatmap_streams_matrix(monkeypatch):
    _patch_data(monkeypatch)
    deps = mk_deps([
        {"tool": "heatmap", "args": {"universe": "dow30"}},
        {"done": True, "message": "corr", "best_id": "r1"},
    ])
    frames = drain("show the factor correlation heatmap", {}, deps)
    res = next(f for f in frames if f["type"] == "result")
    assert res["kind"] == "heatmap" and "Correlation heatmap" in res["label"]
    assert res["data"]["factors"] == ["momentum", "value"]
    assert res["data"]["matrix"][0][0] == 1.0


def test_drill_accepts_custom_factor(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(fma.fm, "known_factor_keys", lambda store: {"momentum", "my_custom"})
    deps = mk_deps([
        {"tool": "drill", "args": {"factor": "my_custom", "universe": "dow30"}},
        {"done": True, "message": "ok", "best_id": "r1"},
    ])
    frames = drain("drill into my_custom", {}, deps)
    res = next(f for f in frames if f["type"] == "result")
    assert res["params"]["factor"] == "my_custom"  # not coerced to momentum


def test_factors_tool_lists_directory(monkeypatch):
    _patch_data(monkeypatch)
    monkeypatch.setattr(fma.fm, "_custom_index", lambda store: {"my_custom": {}})
    monkeypatch.setattr(fma.fm, "_engine_names", lambda store: ["alpha101", "linked_one"])
    deps = mk_deps([
        {"tool": "factors", "args": {}},
        {"done": True, "message": "ok", "best_id": None},
    ])
    frames = drain("what factors are available?", {}, deps)
    obs = [f for f in frames if f["type"] == "obs"]
    assert obs and "my_custom" in obs[0]["text"] and "linked_one" in obs[0]["text"]


def test_system_prompt_lists_custom_factors(monkeypatch):
    monkeypatch.setattr(fma.fm, "_custom_index", lambda store: {"my_custom": {}})
    monkeypatch.setattr(fma.fm, "_engine_names", lambda store: [])
    monkeypatch.setattr(fma.fm, "list_monitors", lambda store: {"monitors": []})
    deps = mk_deps([])
    text = fma._system(deps, {})
    assert "my_custom" in text


def test_history_streams_history(monkeypatch, store):
    _patch_data(monkeypatch)
    deps = mk_deps([
        {"tool": "history", "args": {"name": "Dow"}},
        {"done": True, "message": "hist", "best_id": "r1"},
    ], store=store)
    frames = drain("show history for Dow", {}, deps)
    res = next(f for f in frames if f["type"] == "result")
    assert res["kind"] == "history" and "History ·" in res["label"]


def test_tool_failure_is_non_fatal(monkeypatch):
    state = {"calls": 0}

    def flaky(*a, **k):
        state["calls"] += 1
        if state["calls"] == 1:
            raise ValueError("insufficient data for universe 'dow30'")
        return _fake_board(*a, **k)

    _patch_data(monkeypatch, scorecard=flaky)
    deps = mk_deps([
        {"tool": "rank", "args": {"universe": "dow30"}},  # fails → obs(ok False)
        {"tool": "rank", "args": {"universe": "dow30"}},  # succeeds → result
        {"done": True, "message": "ok", "best_id": "r1"},
    ])
    frames = drain("rank, retry on failure", {}, deps)

    obs = [f for f in frames if f["type"] == "obs"]
    assert obs and obs[0]["ok"] is False
    assert any(f["type"] == "result" for f in frames)
    assert frames[-1]["type"] == "done"


def test_unparseable_reply_recovers(monkeypatch):
    _patch_data(monkeypatch)
    deps = mk_deps([
        "this is not json {",            # unparseable → skipped
        {"done": True, "message": "ok", "best_id": None},
    ])
    frames = drain("garbled then done", {}, deps)
    assert frames[-1]["type"] == "done"


def test_unknown_tool_continues(monkeypatch):
    _patch_data(monkeypatch)
    deps = mk_deps([
        {"tool": "frobnicate", "args": {}},
        {"done": True, "message": "ok", "best_id": None},
    ])
    frames = drain("do something weird", {}, deps)
    assert not any(f["type"] == "result" for f in frames)
    assert frames[-1]["type"] == "done"


def test_max_results_cap(monkeypatch):
    _patch_data(monkeypatch)
    # never emit done; the loop must cap results at _MAX_RESULTS and stop itself.
    deps = mk_deps([{"tool": "rank", "args": {"universe": "dow30"}} for _ in range(fma._MAX_STEPS + 2)])
    frames = drain("keep ranking forever", {}, deps)

    results = [f for f in frames if f["type"] == "result"]
    assert len(results) == fma._MAX_RESULTS, [f["type"] for f in frames]
    assert frames[-1]["type"] == "done"


# ── WebSocket route ──────────────────────────────────────────────────────────────────
def test_ws_route_streams_result_then_done(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import factor_monitor as fmr

    s = TerminalStore(tmp_path / "ws.sqlite")
    s.init()
    script = [
        {"tool": "rank", "args": {"universe": "dow30", "horizon": 5, "q": 5}},
        {"done": True, "message": "ok", "best_id": "r1"},
    ]
    monkeypatch.setattr(fmr, "get_llm_client", lambda: StubLLM(script))
    monkeypatch.setattr(fmr, "get_llm_model", lambda: "stub")
    monkeypatch.setattr(fmr, "get_store", lambda: s)
    monkeypatch.setattr(fma.fm, "scorecard", _fake_board)

    with TestClient(app).websocket_connect("/api/factor-monitor/agent") as ws:
        ws.send_json({"op": "run", "goal": "rank the factors", "context": {}})
        frames: list[dict] = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["type"] in ("done", "error"):
                break

    types = [f["type"] for f in frames]
    assert "result" in types, frames
    assert frames[-1]["type"] == "done"
    res = next(f for f in frames if f["type"] == "result")
    assert res["kind"] == "board" and "Leaderboard" in res["label"]


def test_ws_route_rejects_bad_op():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app).websocket_connect("/api/factor-monitor/agent") as ws:
        ws.send_json({"op": "nope"})
        f = ws.receive_json()
    assert f["type"] == "error" and "expected op 'run'" in f["detail"]


# ── real-data integration (skips if uncached) ────────────────────────────────────────
def test_rank_real_data_sorted_by_ic_ir(providers):
    deps = mk_deps([
        {"tool": "rank", "args": {"universe": "dow30", "horizon": 5, "q": 5}},
        {"done": True, "message": "ok", "best_id": "r1"},
    ], store=None, providers=providers)
    frames = drain("rank factors on the dow", {}, deps)

    results = [f for f in frames if f["type"] == "result"]
    if not results:
        obs = " ".join(f.get("text", "") for f in frames if f["type"] == "obs")
        if "insufficient data" in obs:
            pytest.skip(f"dow30 not cached: {obs}")
        pytest.fail(f"no result frame and no insufficient-data obs: {frames}")

    board = results[0]["data"]
    assert board["rows"] and board["n_instruments"] > 5
    irs = [r["ic_ir"] for r in board["rows"]]
    assert irs == sorted(irs, reverse=True)
