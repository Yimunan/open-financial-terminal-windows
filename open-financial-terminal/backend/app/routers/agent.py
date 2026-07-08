"""Agent workflow: node palette, streaming graph runs, saved graphs.

The graph runs in-process via a dynamically-built LangGraph (`services/agent_graph`). The
WS endpoint mirrors the simple per-connection pattern of `/api/ws/chat`: the client sends a
graph spec + seed, the server streams node frames then `{type:"done"}`.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.deps import (
    get_broker,
    get_data_manager,
    get_fundamentals_provider,
    get_fundamentals_store,
    get_llm_client,
    get_llm_model,
    get_store,
)
from app.config import get_engine_settings, get_terminal_settings
from app.services import agent_assistant as aa
from app.services import agent_coder as acoder
from app.services import agent_graph as ag
from app.services import agent_nodes as an
from app.services import scenario as sc
from app.store import TerminalStore

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _deps() -> an.AgentDeps:
    return an.AgentDeps(
        dm=get_data_manager(),
        fstore=get_fundamentals_store(),
        fprov=get_fundamentals_provider(),
        llm=get_llm_client(),
        model=get_llm_model(),
        llm_base_url=get_engine_settings().llm_base_url,
        broker=get_broker(),
        store=get_store(),
        committee_base_url=get_terminal_settings().committee_base_url,
    )


@router.get("/node-types")
def node_types(store: TerminalStore = Depends(get_store)) -> dict:
    """Node palette. The Committee node's options are filled live from the Committee module so a
    user picks a committee they created there rather than typing its name."""
    import copy

    from app.routers.committee import PRESETS

    types = copy.deepcopy(an.node_types())
    names: list[str] = []
    for n in [p["name"] for p in PRESETS] + [t["name"] for t in store.list_committee_templates()]:
        if n not in names:
            names.append(n)
    for t in types:
        if t["key"] != "committee":
            continue
        for p in t["params"]:
            if p["key"] == "committee" and names:
                p["options"] = names
                if p.get("default") not in names:
                    p["default"] = names[0]
    return {"node_types": types}


# ── AI assistant: edit the whole workflow from natural language ───────────────────
class AssistIn(BaseModel):
    spec: dict
    message: str


@router.post("/assist")
def assist(body: AssistIn) -> dict:
    deps = _deps()
    return aa.assist(deps.llm, deps.model, body.spec, body.message)


# ── saved graphs ─────────────────────────────────────────────────────────────────
class GraphIn(BaseModel):
    spec: dict


@router.get("/graphs")
def list_graphs(store: TerminalStore = Depends(get_store)) -> dict:
    return {"graphs": store.list_agent_graphs()}


@router.get("/graphs/{name}")
def get_graph(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    g = store.get_agent_graph(name)
    if g is None:
        raise HTTPException(404, f"no graph named '{name}'")
    return g


@router.put("/graphs/{name}")
def save_graph(name: str, body: GraphIn, store: TerminalStore = Depends(get_store)) -> dict:
    store.save_agent_graph(name, body.spec)
    return {"ok": True, "name": name}


@router.delete("/graphs/{name}")
def delete_graph(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    store.remove_agent_graph(name)
    return {"ok": True}


# ── scenarios (named variable presets + market shocks) ────────────────────────────
class ScenarioIn(BaseModel):
    description: str = ""
    variables: dict = {}
    shocks: dict = {}


@router.get("/scenarios")
def list_scenarios(store: TerminalStore = Depends(get_store)) -> dict:
    return sc.list_scenarios(store)


@router.put("/scenarios/{name}")
def save_scenario(name: str, body: ScenarioIn, store: TerminalStore = Depends(get_store)) -> dict:
    sc.save_scenario(store, name, body.model_dump())
    return {"ok": True, "name": name}


@router.delete("/scenarios/{name}")
def delete_scenario(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    sc.remove_scenario(store, name)
    return {"ok": True}


# ── streaming run ──────────────────────────────────────────────────────────────
@router.websocket("/run")
async def run_ws(ws: WebSocket) -> None:
    """`{op:"run", spec, seed, context?, scenario?}` → stream node frames then `{type:"done"}`.

    ``context`` (inline scenario variables + shocks) and/or a named ``scenario`` (loaded from the
    store) form the run context that overrides node configs.
    """
    await ws.accept()
    deps = _deps()
    store = get_store()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("op") != "run":
                await ws.send_json({"type": "error", "detail": "expected op 'run'"})
                continue
            spec = msg.get("spec", {})
            seed = msg.get("seed", {})
            context = sc.build_context(
                store, scenario=msg.get("scenario"), context=msg.get("context"), seed=seed,
            )

            # Control plane: while the run streams, a separate task reads the socket for
            # {op:"stop"|"pause"|"resume"}. `gate` (set=run, clear=paused) suspends the graph at
            # node boundaries; `abort` cancels it. Send + receive run concurrently (full-duplex) —
            # only the receives are serialized (the outer loop isn't receiving during a run).
            gate = asyncio.Event(); gate.set()
            abort = asyncio.Event()

            async def _control() -> None:
                try:
                    while True:
                        ctl = await ws.receive_json()
                        op = ctl.get("op")
                        if op == "stop":
                            abort.set()
                            return
                        if op == "pause":
                            gate.clear()
                        elif op == "resume":
                            gate.set()
                except WebSocketDisconnect:
                    abort.set()  # client vanished mid-run → cancel the graph

            ctrl = asyncio.create_task(_control())
            try:
                async for frame in ag.arun_stream(spec, seed, deps, context, gate, abort):
                    try:
                        await ws.send_json(frame)
                    except (WebSocketDisconnect, RuntimeError):
                        abort.set()
                        break
            except Exception as e:  # noqa: BLE001 - never kill the socket on a run error
                with contextlib.suppress(Exception):
                    await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
            finally:
                ctrl.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ctrl
    except WebSocketDisconnect:
        return


@router.websocket("/code")
async def code_ws(ws: WebSocket) -> None:
    """`{op:"code", spec, goal}` → stream the agent's tool steps / observations / spec edits."""
    await ws.accept()
    deps = _deps()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("op") != "code":
                await ws.send_json({"type": "error", "detail": "expected op 'code'"})
                continue
            spec = msg.get("spec", {})
            goal = msg.get("goal", "")
            try:
                async for frame in acoder.arun_coder(spec, goal, deps):
                    await ws.send_json(frame)
            except Exception as e:  # noqa: BLE001 - never kill the socket on an agent error
                await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        return
