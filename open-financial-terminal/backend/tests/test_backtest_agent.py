"""Integration tests for the chat-driven backtest agent.

These exercise the real stack — the `arun_backtest_agent` ReAct loop, the param
validation/clamping, and the actual `backtest.run_backtest` / `strategy_lab.run_lab` engines over
the local data lake — with only the LLM stubbed (scripted JSON actions). The final test drives the
real WebSocket route (`/api/backtest/agent`) through a FastAPI TestClient, so the router + deps +
agent + engine all run together.

Run: `cd backend && pytest tests/ -v`  (needs the local dow30 data the terminal already caches).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import deps as appdeps
from app.services import backtest_agent as bta


class StubLLM:
    """Returns scripted JSON actions in order, ignoring the prompt (the loop drives the rest)."""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[str] = []

    def complete(self, system: str, user: str, model=None, temperature: float = 0.0) -> str:
        self.calls.append(user)
        return json.dumps(self.script.pop(0))


@pytest.fixture(scope="module")
def providers():
    return (
        appdeps.get_data_manager(),
        appdeps.get_fundamentals_store(),
        appdeps.get_fundamentals_provider(),
    )


def mk_deps(providers, script: list[dict]) -> bta.BTDeps:
    dm, fstore, fprov = providers
    return bta.BTDeps(dm=dm, fstore=fstore, fprov=fprov, llm=StubLLM(script), model="stub")


def drain(engine: str, goal: str, context: dict, deps: bta.BTDeps) -> list[dict]:
    async def _run() -> list[dict]:
        return [f async for f in bta.arun_backtest_agent(engine, goal, context, deps)]

    return asyncio.run(_run())


# ── factor engine ────────────────────────────────────────────────────────────────
def test_factor_single_run_streams_full_result(providers):
    deps = mk_deps(
        providers,
        [
            {"tool": "run", "args": {"universe": "dow30", "factor": "momentum", "mode": "long_short", "years": 3}},
            {"done": True, "message": "done", "best_id": "r1"},
        ],
    )
    frames = drain("factor", "momentum long-short on the dow, 3 years", {}, deps)
    runs = [f for f in frames if f["type"] == "run"]
    done = [f for f in frames if f["type"] == "done"]

    assert len(runs) == 1, frames
    r = runs[0]
    assert r["id"] == "r1"
    assert "momentum" in r["label"] and "dow30" in r["label"]
    assert r["metrics"]["sharpe"] is not None
    # the run carries the FULL BacktestResponse for the dashboard
    assert {"metrics", "equity_curve", "drawdown_curve", "robustness"} <= set(r["result"].keys())
    assert done and done[0]["best_id"] == "r1"


def test_factor_clamps_invalid_params(providers):
    deps = mk_deps(
        providers,
        [
            {"tool": "run", "args": {"universe": "NOPE", "factor": "bogus", "mode": "weird", "top_pct": 9, "years": 99}},
            {"done": True, "message": "done", "best_id": "r1"},
        ],
    )
    frames = drain("factor", "nonsense", {}, deps)
    runs = [f for f in frames if f["type"] == "run"]

    assert len(runs) == 1, frames
    p = runs[0]["params"]
    assert p["factor"] == "momentum"  # unknown factor clamped to default
    assert p["universe"] == "dow30"  # unknown universe clamped
    assert p["mode"] == "long_short"  # unknown mode clamped
    assert 0.05 <= p["top_pct"] <= 0.5  # out-of-range clamped
    assert 1 <= p["years"] <= 10


# ── strategy-lab engine ──────────────────────────────────────────────────────────
def test_lab_run_converts_pct_and_streams_trades(providers):
    deps = mk_deps(
        providers,
        [
            {
                "tool": "run",
                "args": {
                    "strategy": "rsi_reversion",
                    "params": {"period": 14, "low": 30, "high": 70},
                    "direction": "both",
                    "sl_pct": 2,  # "2" should be read as 2% -> 0.02
                    "tp_pct": 4,
                },
            },
            {"done": True, "message": "done", "best_id": "r1"},
        ],
    )
    frames = drain("lab", "RSI reversion with a 2% stop and 4% target", {"symbol": "AAPL", "timeframe": "1d"}, deps)
    runs = [f for f in frames if f["type"] == "run"]

    assert len(runs) == 1, frames
    r = runs[0]
    assert r["params"]["sl_pct"] == 0.02 and r["params"]["tp_pct"] == 0.04  # _pct conversion
    assert r["params"]["symbol"] == "AAPL"
    # LabResult shape for the dashboard
    assert {"trades", "stats", "candles", "equity_curve", "markers"} <= set(r["result"].keys())
    assert r["metrics"]["trades"] is not None


# ── full WebSocket route (router + deps + agent + engine) ────────────────────────
def test_ws_route_streams_run_then_done(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import backtest as btr

    script = [
        {"tool": "run", "args": {"universe": "dow30", "factor": "momentum", "mode": "long_short", "years": 3}},
        {"done": True, "message": "ok", "best_id": "r1"},
    ]
    monkeypatch.setattr(btr, "get_llm_client", lambda: StubLLM(script))

    with TestClient(app).websocket_connect("/api/backtest/agent") as ws:
        ws.send_json({"op": "run", "engine": "factor", "goal": "momentum on the dow", "context": {}})
        frames: list[dict] = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["type"] in ("done", "error"):
                break

    types = [f["type"] for f in frames]
    assert "run" in types, frames
    assert frames[-1]["type"] == "done"
    run = next(f for f in frames if f["type"] == "run")
    assert "momentum" in run["label"] and "metrics" in run["result"]
