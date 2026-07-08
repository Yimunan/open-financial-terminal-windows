"""Chart Studio — chat-driven chart creation agent (WebSocket)."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.deps import get_data_manager, get_llm_client, get_llm_model, get_macro_store, get_rates_store
from app.services import chart_agent as ca

router = APIRouter(prefix="/api", tags=["chart"])


@router.websocket("/chart/agent")
async def chart_agent_ws(ws: WebSocket) -> None:
    """`{op:"run", message, context}` → stream thought / chart / obs / done frames."""
    await ws.accept()
    deps = ca.ChartDeps(
        dm=get_data_manager(),
        macro_store=get_macro_store(),
        rates_store=get_rates_store(),
        llm=get_llm_client(),
        model=get_llm_model(),
    )
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("op") != "run":
                await ws.send_json({"type": "error", "detail": "expected op 'run'"})
                continue
            message = msg.get("message", "")
            context = msg.get("context") or {}
            try:
                async for frame in ca.arun_chart_studio_agent(message, context, deps):
                    await ws.send_json(frame)
            except Exception as e:  # noqa: BLE001 - never kill the socket on a run error
                await ws.send_json({"type": "error", "detail": f"{type(e).__name__}: {e}"})
    except WebSocketDisconnect:
        return
