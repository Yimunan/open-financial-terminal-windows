"""Integration tests for the portfolio-construction engine of the backtest agent.

Chat → agent picks ONE existing source (factor / strategy / model) → runs its backtest →
turns the resulting weights into a SAVED portfolio → values it into shares, streaming the
standard `thought / run / obs / done` frames. Mirrors `test_backtest_agent.py`: a scripted
`StubLLM`, a `drain()` helper, the real `DataManager` over the local dow30 lake, and a TEMP
`TerminalStore` so saved models/portfolios never touch the real `oft.sqlite`.

Skips the data-dependent assertions when dow30 isn't cached.

Run: `cd backend && pytest tests/test_portfolio_agent.py -v`
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import deps as appdeps
from app.services import backtest_agent as bta
from app.services import portfolio_book as pb
from app.services import registry as reg
from app.store import TerminalStore


class StubLLM:
    """Returns scripted JSON actions in order, ignoring the prompt."""

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


@pytest.fixture
def store(tmp_path):
    s = TerminalStore(tmp_path / "oft.sqlite")
    s.init()
    return s


def mk_deps(providers, store, script: list[dict]) -> bta.BTDeps:
    dm, fstore, fprov = providers
    return bta.BTDeps(dm=dm, fstore=fstore, fprov=fprov, llm=StubLLM(script), model="stub", store=store)


def drain(goal: str, context: dict, deps: bta.BTDeps) -> list[dict]:
    async def _run() -> list[dict]:
        return [f async for f in bta.arun_backtest_agent("portfolio", goal, context, deps)]

    return asyncio.run(_run())


def _skip_if_no_data(frames: list[dict]) -> None:
    if any(f["type"] == "run" for f in frames):
        return
    obs = " ".join(f.get("text", "") for f in frames if f["type"] == "obs")
    if "insufficient data" in obs or "no holdings" in obs or "no returns" in obs:
        pytest.skip(f"dow30 data not cached: {obs}")


def test_portfolio_from_factor_saves_and_backtests(providers, store):
    deps = mk_deps(providers, store, [
        {"tool": "run", "args": {
            "source": "factor", "factor": "momentum", "universe": "dow30",
            "mode": "long_short", "years": 3, "capital": 1_000_000, "save_as": "itest_pf",
        }},
        {"done": True, "message": "built", "best_id": "r1"},
    ])
    frames = drain("build a momentum long-short portfolio on the dow", {}, deps)
    _skip_if_no_data(frames)

    runs = [f for f in frames if f["type"] == "run"]
    assert len(runs) == 1, frames
    r = runs[0]
    assert r["id"] == "r1"
    assert "portfolio" in r["label"] and "momentum" in r["label"]
    assert r["metrics"]["sharpe"] is not None and r["metrics"]["n_holdings"] > 0

    res = r["result"]
    # the standard backtest dashboard is present...
    assert {"metrics", "equity_curve", "drawdown_curve", "top_weights"} <= set(res)
    # ...plus the constructed + saved portfolio and its share allocation
    pf = res["portfolio"]
    assert pf["saved"] is True and pf["name"] == "itest_pf" and pf["source"] == "factor"
    assert pf["allocations"] and "exposures" in pf
    assert "rows" in res["allocation"] and res["allocation"]["capital"] == 1_000_000

    # persisted in the Portfolios module
    saved = {p["name"] for p in pb.list_portfolios(store)["portfolios"]}
    assert "itest_pf" in saved

    done = [f for f in frames if f["type"] == "done"]
    assert done and done[0]["best_id"] == "r1"


def test_portfolio_from_model_bundle(providers, store):
    reg.save_model(store, "itest_model", {
        "factor": "momentum", "strategy": "", "universe": "dow30", "mode": "long_short",
        "params": {}, "tags": [], "notes": "",
    })
    deps = mk_deps(providers, store, [
        {"tool": "run", "args": {"source": "model", "model": "itest_model", "capital": 500_000}},
        {"done": True, "message": "built", "best_id": "r1"},
    ])
    frames = drain("make a portfolio from my itest_model", {}, deps)
    _skip_if_no_data(frames)

    runs = [f for f in frames if f["type"] == "run"]
    assert len(runs) == 1, frames
    pf = runs[0]["result"]["portfolio"]
    assert pf["source"] == "model" and pf["ident"] == "itest_model"
    assert "dow30" in runs[0]["label"]  # universe came from the bundle
    assert any(p["name"] == pf["name"] for p in pb.list_portfolios(store)["portfolios"])


def test_unknown_model_fails_gracefully(providers, store):
    deps = mk_deps(providers, store, [
        {"tool": "run", "args": {"source": "model", "model": "does_not_exist"}},
        {"done": True, "message": "n/a", "best_id": None},
    ])
    frames = drain("portfolio from a missing model", {}, deps)
    assert not [f for f in frames if f["type"] == "run"]
    obs = [f for f in frames if f["type"] == "obs"]
    assert obs and obs[0]["ok"] is False and "unknown model" in obs[0]["text"]
    assert frames[-1]["type"] == "done"


def test_strategy_stub_is_graceful(providers, store):
    # qhfi's `momentum` strategy is a stub (raise NotImplementedError) — the loop must surface a
    # clean failure observation and finish, not crash.
    deps = mk_deps(providers, store, [
        {"tool": "run", "args": {"source": "strategy", "strategy": "momentum", "universe": "dow30"}},
        {"done": True, "message": "n/a", "best_id": None},
    ])
    frames = drain("portfolio from the momentum strategy", {}, deps)
    obs = [f for f in frames if f["type"] == "obs"]
    # either a stub error or an insufficient-data error, but never a crash and never a run frame
    assert not [f for f in frames if f["type"] == "run"]
    assert obs and obs[0]["ok"] is False
    assert frames[-1]["type"] == "done"


def test_ws_route_portfolio(monkeypatch, tmp_path, providers):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import backtest as btr

    s = TerminalStore(tmp_path / "ws.sqlite")
    s.init()
    script = [
        {"tool": "run", "args": {
            "source": "factor", "factor": "momentum", "universe": "dow30",
            "mode": "long_short", "years": 3, "save_as": "ws_pf",
        }},
        {"done": True, "message": "ok", "best_id": "r1"},
    ]
    monkeypatch.setattr(btr, "get_llm_client", lambda: StubLLM(script))
    monkeypatch.setattr(btr, "get_store", lambda: s)

    with TestClient(app).websocket_connect("/api/backtest/agent") as ws:
        ws.send_json({"op": "run", "engine": "portfolio", "goal": "momentum portfolio on the dow", "context": {}})
        frames: list[dict] = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["type"] in ("done", "error"):
                break

    types = [f["type"] for f in frames]
    if "run" not in types:  # no cached data → graceful obs then done; route still works
        assert types[-1] == "done"
        return
    run = next(f for f in frames if f["type"] == "run")
    assert run["result"]["portfolio"]["saved"] is True
    assert frames[-1]["type"] == "done"
