"""Tests for the grounded assistant agent (services.assistant_agent) + the /api/ws/chat route.

Hermetic: the planner LLM, the read-only tools, and the answer token-stream are all stubbed, so the
test pins the *frame contract* the Assistant widget depends on after the bento refactor — `tool`
frames feed the Processing sub-window, `token` frames feed the Chat sub-window — without the network
or the local model.

Run: cd backend && .venv\\Scripts\\python -m pytest tests/test_assistant_agent.py -q
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services import assistant as asst
from app.services import assistant_agent as agent
from app.services import assistant_tools as tools
from app.services import mcp_client
from app.services.assistant_tools import ToolContext


class _StubLLM:
    """Stand-in for qhfi's LLMClient — `structured` returns a canned plan, counts calls."""

    def __init__(self, plan, raise_on_plan: bool = False):
        self._plan = plan
        self.raise_on_plan = raise_on_plan
        self.calls = 0

    def structured(self, system, user, schema, model=None):
        self.calls += 1
        if self.raise_on_plan:
            raise RuntimeError("planner boom")
        return {"tools": list(self._plan)}


def _ctx(plan, raise_on_plan: bool = False) -> ToolContext:
    # Only llm + model are touched once the IO boundaries are stubbed; the data deps stay None.
    return ToolContext(dm=None, fstore=None, fprov=None,
                       llm=_StubLLM(plan, raise_on_plan), model="stub-model")


def _drain(ctx, messages, symbol="AAPL"):
    async def go():
        return [f async for f in agent.run(ctx, messages, symbol, "http://stub/v1", None)]
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def _stub_io(monkeypatch):
    """Cut every external dependency of agent.run: external MCP discovery, the tool dispatcher, and
    the answer token-stream. Leaves the planner + frame assembly real."""
    async def _no_ext():
        return []
    monkeypatch.setattr(mcp_client, "list_external_tools", _no_ext)

    # Tools return canned grounded text + a structured `data` payload (the `summary`) without
    # touching yfinance/qhfi. Echo the args so a test can assert what got dispatched.
    def _run_tool(name, ctx, args):
        return (f"{name} {args.get('symbol', '')} last=200.00", {"tool": name, "args": args, "price": 200.0})
    monkeypatch.setattr(tools, "run_tool", _run_tool)

    # The grounded answer streams two token deltas.
    async def _stream(base_url, model, chat, api_key=None):
        for tok in ("AAPL ", "is 200."):
            yield tok
    monkeypatch.setattr(asst, "stream_chat", _stream)


# ── agent-level: the real run() with stubbed IO ──────────────────────────────────────────────

def test_grounded_run_streams_tool_then_tokens_then_done():
    ctx = _ctx([{"name": "get_quote", "symbol": "AAPL"}])
    frames = _drain(ctx, [{"role": "user", "content": "price of AAPL?"}])

    kinds = [f["type"] for f in frames]
    # one tool fetch, then the streamed answer, then done — in that order.
    assert kinds == ["tool", "token", "token", "done"]

    tool = frames[0]
    assert tool["name"] == "get_quote"
    assert tool["args"] == {"symbol": "AAPL"}        # what the Processing window renders
    assert tool["summary"]["price"] == 200.0          # structured data rode along

    answer = "".join(f["text"] for f in frames if f["type"] == "token")
    assert answer == "AAPL is 200."                   # what the Chat window renders


def test_tool_frame_omits_none_valued_args():
    # The planner often emits unset optional fields as null; the tool frame must drop them so the
    # Processing line reads "get_quote · symbol=AAPL", not a wall of "asset=None period=None".
    ctx = _ctx([{"name": "get_quote", "symbol": "AAPL", "asset": None, "period": None}])
    frames = _drain(ctx, [{"role": "user", "content": "AAPL price"}])
    tool = next(f for f in frames if f["type"] == "tool")
    assert tool["args"] == {"symbol": "AAPL"}


def test_multiple_tools_each_emit_a_frame_in_plan_order():
    ctx = _ctx([
        {"name": "get_quote", "symbol": "AAPL"},
        {"name": "get_fundamentals", "symbol": "AAPL"},
    ])
    frames = _drain(ctx, [{"role": "user", "content": "AAPL price and P/E?"}])
    tool_names = [f["name"] for f in frames if f["type"] == "tool"]
    assert tool_names == ["get_quote", "get_fundamentals"]
    assert frames[-1]["type"] == "done"


def test_conceptual_question_fetches_no_tools():
    # Empty plan (a pure concept question) → straight to the streamed answer, no tool frames.
    ctx = _ctx([])
    frames = _drain(ctx, [{"role": "user", "content": "what is a P/E ratio?"}])
    assert [f["type"] for f in frames] == ["token", "token", "done"]
    assert not any(f["type"] == "tool" for f in frames)


def test_planner_failure_degrades_to_plain_answer():
    # A planner exception must not break the stream — _plan swallows it and the agent still answers.
    ctx = _ctx([{"name": "get_quote", "symbol": "AAPL"}], raise_on_plan=True)
    frames = _drain(ctx, [{"role": "user", "content": "price of AAPL?"}])
    assert [f["type"] for f in frames] == ["token", "token", "done"]


def test_unknown_planned_tool_is_dropped():
    # The planner can hallucinate a tool name; it must be filtered before dispatch.
    ctx = _ctx([{"name": "teleport", "symbol": "AAPL"}, {"name": "get_quote", "symbol": "AAPL"}])
    frames = _drain(ctx, [{"role": "user", "content": "AAPL?"}])
    tool_names = [f["name"] for f in frames if f["type"] == "tool"]
    assert tool_names == ["get_quote"]


# ── route-level: the real agent driven through the /api/ws/chat WebSocket ─────────────────────

def test_ws_chat_route_streams_grounded_frames(monkeypatch):
    """End-to-end through the WebSocket the frontend opens: receive → real agent.run → send_json,
    with only the LLM/deps stubbed. Pins that every agent frame is forwarded verbatim to the socket."""
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import app
    from app.routers import assistant as route

    plan_ctx = _ctx([{"name": "get_quote", "symbol": "AAPL"}])
    monkeypatch.setattr(route, "_tool_context", lambda: plan_ctx)
    monkeypatch.setattr(route, "get_llm_model", lambda: "stub-model")

    class _Eng:
        llm_base_url = "http://stub/v1"
        llm_api_key = None
    monkeypatch.setattr(route, "get_engine_settings", lambda: _Eng())
    # ws_chat also resolves the model via deps at connect; keep it off the network.
    monkeypatch.setattr(deps, "get_llm_model", lambda: "stub-model", raising=False)

    client = TestClient(app)
    with client.websocket_connect("/api/ws/chat") as ws:
        ws.send_json({"messages": [{"role": "user", "content": "price of AAPL?"}], "symbol": "AAPL"})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["type"] in ("done", "error"):
                break

    kinds = [f["type"] for f in frames]
    assert kinds[0] == "tool" and kinds[-1] == "done"
    assert frames[0]["name"] == "get_quote"
    assert "".join(f["text"] for f in frames if f["type"] == "token") == "AAPL is 200."


# ── ReAct CONTROL loop: drive the modules, with the client action round-trip ──────────────────

class _ScriptLLM:
    """`complete` returns scripted JSON actions in order (then defaults to {"answer":true}). With
    `always`, it returns that action forever — to exercise the step cap."""

    def __init__(self, actions, always=None):
        self._actions = list(actions)
        self.always = always
        self.calls = 0

    def complete(self, system, user, model=None, temperature=0.4):
        self.calls += 1
        if self.always is not None:
            return json.dumps(self.always)
        return json.dumps(self._actions.pop(0)) if self._actions else json.dumps({"answer": True})


def _ctx_react(actions, always=None):
    return ToolContext(dm=None, fstore=None, fprov=None,
                       llm=_ScriptLLM(actions, always), model="stub-model")


_CAPS = {
    "actions": list(agent.CLIENT_ACTIONS),
    "widgets": [{"type": "chart", "description": "price chart", "params": {"symbol": "ticker"}}],
}


def _drive_react(ctx, messages, observations=(), symbol="AAPL", workspace=None, capabilities=None):
    """Drive arun_react like the route does: feed each scripted client observation back into the
    action frame via gen.asend."""
    obs = iter(observations)

    async def go():
        frames, send_val = [], None
        gen = agent.arun_react(ctx, messages, symbol, workspace, capabilities or _CAPS, "http://x/v1", None)
        try:
            while True:
                frame = await gen.asend(send_val)
                frames.append(frame)
                send_val = next(obs, {"ok": True, "result": "ok"}) if frame.get("type") == "action" else None
        except StopAsyncIteration:
            pass
        finally:
            await gen.aclose()
        return frames

    return asyncio.run(go())


def test_react_open_widget_action_then_answers():
    ctx = _ctx_react([
        {"thought": "open a chart", "tool": "open_widget", "args": {"type": "chart", "params": {"symbol": "NVDA"}}},
        {"answer": True},
    ])
    frames = _drive_react(ctx, [{"role": "user", "content": "open a chart for NVDA"}],
                          observations=[{"ok": True, "result": "opened Chart for NVDA"}])
    kinds = [f["type"] for f in frames]
    assert "action" in kinds and kinds[-1] == "done"
    act = next(f for f in frames if f["type"] == "action")
    assert act["name"] == "open_widget" and act["id"] and act["args"]["type"] == "chart"
    # a thought was surfaced and the final answer streamed after the action
    assert kinds.index("thought") < kinds.index("action") < kinds.index("token")
    assert "".join(f["text"] for f in frames if f["type"] == "token") == "AAPL is 200."


def test_react_server_tool_runs_inline_without_roundtrip():
    # A data tool resolves on the backend → a `tool` frame (no `action`, no client observation).
    ctx = _ctx_react([{"tool": "get_quote", "args": {"symbol": "AAPL"}}, {"answer": True}])
    frames = _drive_react(ctx, [{"role": "user", "content": "price then done"}])
    kinds = [f["type"] for f in frames]
    assert "tool" in kinds and "action" not in kinds and kinds[-1] == "done"
    assert next(f for f in frames if f["type"] == "tool")["name"] == "get_quote"


def test_react_answers_immediately_with_no_steps():
    ctx = _ctx_react([{"answer": True}])
    frames = _drive_react(ctx, [{"role": "user", "content": "what is a P/E ratio?"}])
    assert [f["type"] for f in frames] == ["token", "token", "done"]


def test_react_unknown_action_is_reported_not_executed():
    # A verb outside the allowlist must not dispatch — it becomes an observation, loop still finishes.
    ctx = _ctx_react([{"tool": "delete_everything", "args": {}}, {"answer": True}])
    frames = _drive_react(ctx, [{"role": "user", "content": "nuke it"}])
    kinds = [f["type"] for f in frames]
    assert "action" not in kinds and kinds[-1] == "done"
    assert any(f["type"] == "obs" and "unknown" in f["text"] for f in frames)


def test_react_step_cap_bounds_a_runaway_plan():
    # complete() never says answer → loop must stop after _MAX_STEPS, then still answer.
    ctx = _ctx_react([], always={"tool": "get_quote", "args": {"symbol": "AAPL"}})
    frames = _drive_react(ctx, [{"role": "user", "content": "loop forever"}])
    assert sum(1 for f in frames if f["type"] == "tool") == agent._MAX_STEPS
    assert frames[-1]["type"] == "done"


def test_ws_chat_route_runs_control_loop_with_observation_roundtrip(monkeypatch):
    """The real route + ReAct loop over the WebSocket: server emits an `action`, the client replies
    `{op:'observation', ...}`, and the loop resumes to stream the answer."""
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import app
    from app.routers import assistant as route

    ctx = _ctx_react([
        {"tool": "open_widget", "args": {"type": "chart", "params": {"symbol": "NVDA"}}},
        {"answer": True},
    ])
    monkeypatch.setattr(route, "_tool_context", lambda: ctx)
    monkeypatch.setattr(route, "get_llm_model", lambda: "stub-model")

    class _Eng:
        llm_base_url = "http://stub/v1"
        llm_api_key = None
    monkeypatch.setattr(route, "get_engine_settings", lambda: _Eng())
    monkeypatch.setattr(deps, "get_llm_model", lambda: "stub-model", raising=False)

    client = TestClient(app)
    with client.websocket_connect("/api/ws/chat") as ws:
        ws.send_json({
            "messages": [{"role": "user", "content": "open a chart for NVDA"}],
            "symbol": "AAPL",
            "workspace": {"panels": [], "channels": {}},
            "capabilities": _CAPS,
        })
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["type"] == "action":
                ws.send_json({"op": "observation", "id": f["id"], "ok": True, "result": "opened Chart"})
            if f["type"] in ("done", "error"):
                break

    kinds = [f["type"] for f in frames]
    assert "action" in kinds and kinds[-1] == "done"
    assert next(f for f in frames if f["type"] == "action")["name"] == "open_widget"
    assert "".join(f["text"] for f in frames if f["type"] == "token") == "AAPL is 200."
