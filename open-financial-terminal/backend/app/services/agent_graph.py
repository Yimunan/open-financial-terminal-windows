"""Build and run a LangGraph StateGraph from the UI's visual graph spec.

The frontend sends a spec of nodes (type + config + position) and edges (source→target).
We construct a real `langgraph.graph.StateGraph` dynamically, wrap each node so it emits
running/done/error frames as it executes, and stream those frames to the websocket. v1
supports DAGs (acyclic); each node's body is a fixed Python function from `agent_nodes`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any, AsyncIterator, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.agent_nodes import AgentDeps, _BY_KEY, run_node


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class GraphState(TypedDict):
    seed: dict
    context: dict  # active scenario variables + shocks; overrides node configs in run_node
    outputs: Annotated[dict, _merge]


def _validate(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """Return incoming-edge map; raise ValueError on unknown types, bad refs, or cycles."""
    if not nodes:
        raise ValueError("graph is empty")
    ids = [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate node ids")
    for n in nodes:
        if n["type"] not in _BY_KEY:
            raise ValueError(f"unknown node type '{n['type']}'")
    idset = set(ids)
    for e in edges:
        if e["source"] not in idset or e["target"] not in idset:
            raise ValueError("edge references a missing node")

    incoming = {nid: [e["source"] for e in edges if e["target"] == nid] for nid in ids}
    # Kahn's algorithm — detect cycles
    indeg = {nid: len(incoming[nid]) for nid in ids}
    queue = [nid for nid in ids if indeg[nid] == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for e in edges:
            if e["source"] == cur:
                indeg[e["target"]] -= 1
                if indeg[e["target"]] == 0:
                    queue.append(e["target"])
    if seen != len(ids):
        raise ValueError("graph has a cycle (only DAGs are supported)")
    return incoming


def _make_fn(node: dict, incoming: list[str], deps: AgentDeps, emit, gate: asyncio.Event | None = None):
    nid = node["id"]

    async def fn(state: GraphState) -> dict:
        # Pause gate (set = run, clear = paused): blocks here, at the node boundary, while paused —
        # the finest granularity the LangGraph executor allows (it can't suspend mid-node). A run
        # cancellation (Stop) raises CancelledError into this await and unwinds cleanly.
        if gate is not None:
            await gate.wait()
        await emit({"type": "node", "id": nid, "status": "running"})
        inputs = {src: state["outputs"][src] for src in incoming if src in state["outputs"]}
        context = state.get("context") or {}
        loop = asyncio.get_running_loop()

        def on_progress(frame: dict) -> None:
            # run_node runs in a worker thread; hop its progress frames back onto the loop. Tagged as a
            # `running` node frame with a `progress` payload — clients that only read status ignore it.
            try:
                asyncio.run_coroutine_threadsafe(
                    emit({"type": "node", "id": nid, "status": "running", "progress": frame}), loop
                )
            except RuntimeError:
                pass  # event loop shutting down (run stopped/cancelled)

        try:
            res = await asyncio.to_thread(run_node, node, inputs, deps, context, on_progress)
        except Exception as e:  # noqa: BLE001 - surface node failures to the UI
            await emit({"type": "node", "id": nid, "status": "error", "summary": f"{type(e).__name__}: {e}"})
            raise
        await emit({
            "type": "node", "id": nid, "status": "done",
            "summary": res.get("summary", ""), "value": res.get("value"),
        })
        return {"outputs": {nid: res}}

    return fn


def build(spec: dict, deps: AgentDeps, emit, gate: asyncio.Event | None = None) -> Any:
    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])
    incoming = _validate(nodes, edges)
    outgoing = {n["id"]: [e["target"] for e in edges if e["source"] == n["id"]] for n in nodes}

    g = StateGraph(GraphState)
    for n in nodes:
        g.add_node(n["id"], _make_fn(n, incoming[n["id"]], deps, emit, gate))
    for n in nodes:
        nid = n["id"]
        if not incoming[nid]:
            g.add_edge(START, nid)
        if not outgoing[nid]:
            g.add_edge(nid, END)
    # Wire incoming edges grouped by target. A node with multiple predecessors must JOIN on all of
    # them — langgraph's add_edge([src1, src2, ...], target) waits for ALL sources, running the node
    # exactly once. Adding each edge separately (add_edge(src, target) per edge) instead fires the
    # node once per incoming edge, double-executing every fan-in node.
    for nid, srcs in incoming.items():
        if srcs:
            g.add_edge(srcs[0] if len(srcs) == 1 else srcs, nid)
    return g.compile()


async def arun_stream(
    spec: dict, seed: dict, deps: AgentDeps, context: dict | None = None,
    gate: asyncio.Event | None = None, abort: asyncio.Event | None = None,
) -> AsyncIterator[dict]:
    """Run the compiled graph, yielding node frames as they happen, then a final done/error.

    ``context`` carries the active scenario (variables + shocks); it overrides node configs
    inside ``run_node``. ``gate`` (set = run, clear = paused) suspends the graph at node
    boundaries; setting ``abort`` cancels the in-flight graph task — both let the caller honor
    Pause/Stop from the websocket. Cancelling the underlying task is the load-bearing fix: without
    it, an abandoned run keeps executing nodes server-side.
    """
    q: asyncio.Queue = asyncio.Queue()

    async def emit(frame: dict) -> None:
        await q.put(frame)

    try:
        compiled = build(spec, deps, emit, gate)
    except ValueError as e:
        yield {"type": "error", "detail": str(e)}
        return

    task = asyncio.create_task(
        compiled.ainvoke({"seed": seed, "context": context or {}, "outputs": {}})
    )
    abort_wait = asyncio.create_task(abort.wait()) if abort is not None else None
    stopped = False
    try:
        while True:
            getter = asyncio.create_task(q.get())
            waitset: set = {getter, task}
            if abort_wait is not None:
                waitset.add(abort_wait)
            done, _ = await asyncio.wait(waitset, return_when=asyncio.FIRST_COMPLETED)
            if abort_wait is not None and abort_wait in done:
                getter.cancel()
                stopped = True
                break
            if getter in done:
                yield getter.result()
            else:
                getter.cancel()
            if task.done():
                while not q.empty():
                    yield q.get_nowait()
                break
    finally:
        if abort_wait is not None and not abort_wait.done():
            abort_wait.cancel()
        if not task.done():
            task.cancel()  # cancel the graph so a stopped/disconnected run doesn't run on
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    if stopped:
        yield {"type": "done", "stopped": True}
        return
    exc = task.exception()
    if exc is not None:
        yield {"type": "error", "detail": f"{type(exc).__name__}: {exc}"}
    else:
        yield {"type": "done"}
