"""Autonomous research loop: stream a design→generate→evaluate→reflect cycle, browse past runs.

Mirrors the agent-workflow router (`routers/agent.py`): the WS client sends a goal, the server
streams per-phase frames then `{type:"done"}`. Runs are persisted as they go, so the REST
endpoints can replay any past run's per-iteration dashboards.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.deps import (
    get_data_manager,
    get_fundamentals_provider,
    get_fundamentals_store,
    get_llm_client,
    get_llm_model,
    get_store,
)
from app.services import research_loop as rl
from app.store import TerminalStore

router = APIRouter(prefix="/api/research", tags=["research"])


def _deps() -> rl.RLDeps:
    return rl.RLDeps(
        dm=get_data_manager(),
        fstore=get_fundamentals_store(),
        fprov=get_fundamentals_provider(),
        llm=get_llm_client(),
        model=get_llm_model(),
        store=get_store(),
    )


# ── history ──────────────────────────────────────────────────────────────────────
@router.get("/runs")
def list_runs(store: TerminalStore = Depends(get_store)) -> dict:
    return {"runs": store.list_research_runs()}


@router.get("/runs/{run_id}")
def get_run(run_id: str, store: TerminalStore = Depends(get_store)) -> dict:
    run = store.get_research_run(run_id)
    if run is None:
        raise HTTPException(404, f"no research run '{run_id}'")
    return {"run": run, "iterations": store.list_research_iterations(run_id)}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, store: TerminalStore = Depends(get_store)) -> dict:
    store.remove_research_run(run_id)
    return {"ok": True}


# ── streaming run ──────────────────────────────────────────────────────────────────
@router.websocket("/run")
async def run_ws(ws: WebSocket) -> None:
    """`{op:"run", goal, max_iters?}` → stream phase/design/evaluate/result/iteration/reflect
    frames then `{type:"done"}`. A run row is created up front so partial results survive a
    disconnect."""
    await ws.accept()
    deps = _deps()
    store = get_store()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("op") != "run":
                await ws.send_json({"type": "error", "detail": "expected op 'run'"})
                continue
            goal = str(msg.get("goal", "")).strip() or "find a strategy that passes the promotion scorecard"
            max_iters = msg.get("max_iters", rl._MAX_ITERS)
            run_id = store.create_research_run(goal)
            await ws.send_json({"type": "started", "run_id": run_id, "goal": goal})
            try:
                async for frame in rl.arun_research_loop(goal, deps, max_iters=max_iters, run_id=run_id):
                    await ws.send_json(frame)
            except Exception as e:  # noqa: BLE001 - never kill the socket on a run error
                try:
                    store.finalize_research_run(run_id, {}, status="error")
                except Exception:  # noqa: BLE001
                    pass
                await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        return
