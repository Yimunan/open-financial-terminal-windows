"""LLM assistant endpoints: NL→screen, summarize, and a streaming chat WebSocket."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from qhfi.data.manager import DataManager
from qhfi.research.client import LLMClient

from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider

from app.config import get_engine_settings
from app.deps import (
    get_data_manager,
    get_fundamentals_provider,
    get_fundamentals_store,
    get_llm_client,
    get_llm_model,
    get_store,
)
from app.services import assistant as asst
from app.services import assistant_agent as agent
from app.services import assistant_tools as tools
from app.services import news_router as nr
from app.services import screener as scr
from app.services.assistant_tools import ToolContext
from app.services.market import fetch_bars


def _tool_context() -> ToolContext:
    """Build the read-only ToolContext the same way the chat WebSocket does (deps singletons)."""
    return ToolContext(
        dm=get_data_manager(),
        fstore=get_fundamentals_store(),
        fprov=get_fundamentals_provider(),
        llm=get_llm_client(),
        model=get_llm_model(),
        store=get_store(),
    )

router = APIRouter(prefix="/api", tags=["assistant"])


class AskRequest(BaseModel):
    query: str


@router.post("/ask")
def ask(
    req: AskRequest,
    dm: DataManager = Depends(get_data_manager),
    fstore: FundamentalsStore = Depends(get_fundamentals_store),
    fprov: YFinanceFundamentalsProvider = Depends(get_fundamentals_provider),
    llm: LLMClient = Depends(get_llm_client),
) -> dict:
    """Natural-language query bar: NL → screener params → ranked results."""
    try:
        params = asst.nl_to_screen(llm, req.query, get_llm_model())
    except Exception as e:  # noqa: BLE001 - surface LLM/proxy issues as 502
        raise HTTPException(502, f"LLM error: {type(e).__name__}") from None
    result = scr.run_factor_screen(
        dm, fstore, fprov, params["universe"], params["factor"], int(params.get("limit", 25))
    )
    result["rationale"] = params.get("rationale")
    return result


class SummarizeRequest(BaseModel):
    symbol: str
    asset: str = "equity"


@router.post("/summarize")
def summarize(
    req: SummarizeRequest,
    dm: DataManager = Depends(get_data_manager),
    llm: LLMClient = Depends(get_llm_client),
) -> dict:
    """Summarize a symbol from its recent price action + latest headlines."""
    end = date.today()
    _, bars = fetch_bars(dm, req.symbol, req.asset, end - timedelta(days=120), end)
    px_ctx = "no price data"
    if not bars.empty:
        last = float(bars["close"].iloc[-1])
        chg_30 = (last / float(bars["close"].iloc[-22]) - 1) * 100 if len(bars) > 22 else 0.0
        chg_90 = (last / float(bars["close"].iloc[0]) - 1) * 100 if len(bars) else 0.0
        px_ctx = f"last={last:.2f}, 30d={chg_30:+.1f}%, ~90d={chg_90:+.1f}%"
    headlines = [n["title"] for n in nr.news(req.symbol, limit=8)]
    context = f"Price: {px_ctx}\nHeadlines:\n" + "\n".join(f"- {h}" for h in headlines)
    text = asst.summarize(llm, req.symbol.upper(), context, get_llm_model())
    return {"symbol": req.symbol.upper(), "summary": text}


@router.get("/assistant/tools")
def list_tools() -> dict:
    """The read-only tool catalog — name, description, and argument hint for each tool.

    Powers the standalone MCP server's tool definitions and any client that wants to discover
    what the terminal can fetch.
    """
    return {
        "tools": [
            {"name": name, "desc": meta["desc"], "args": meta["args"]}
            for name, meta in tools.TOOLS.items()
        ]
    }


@router.post("/assistant/tools/{name}")
def run_tool(name: str, args: dict | None = Body(default=None)) -> dict:
    """Run one read-only tool by name and return its grounded result.

    Reuses the exact tool functions + dispatcher the grounded assistant uses (symbol resolution,
    index/crypto proxies, return computation all stay in assistant_tools), so an MCP client gets
    identical results to the in-app assistant. ``run_tool`` never raises — a bad fetch comes back
    as an error string in ``text``; only an unknown tool is a 404.
    """
    if name not in tools.TOOLS:
        raise HTTPException(404, f"unknown tool '{name}'")
    text, data = tools.run_tool(name, _tool_context(), dict(args or {}))
    return {"text": text, "data": data}


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """Grounded streaming chat. Client sends {messages:[{role,content}], symbol?, grounded?}.

    The server plans read-only data tools for the question (quote/performance/fundamentals/news/
    screen/compare), runs them against the real services, then streams the answer grounded in that
    data — emitting {type:'tool', name, args, summary} per fetch, then {type:'token', text} deltas,
    then {type:'done'}. Set grounded=false to fall back to plain ungrounded streaming.

    When the request carries a `capabilities` catalog (+ optional `workspace` snapshot), the server
    runs the ReAct CONTROL loop instead: it can also DRIVE the terminal's modules, emitting
    {type:'action', id, name, args} frames the client executes and answers with
    {op:'observation', id, ok, result} — fed back into the loop.
    """
    await ws.accept()
    eng = get_engine_settings()
    model = get_llm_model()
    ctx = _tool_context()
    try:
        while True:
            req = await ws.receive_json()
            messages = [m for m in req.get("messages", []) if m.get("role") in ("user", "assistant")]
            symbol = req.get("symbol")
            grounded = req.get("grounded", True)
            capabilities = req.get("capabilities")
            try:
                if capabilities is not None:
                    await _run_control_loop(ws, ctx, messages, symbol, req.get("workspace"), capabilities, eng)
                elif grounded:
                    async for frame in agent.run(ctx, messages, symbol, eng.llm_base_url, eng.llm_api_key):
                        await ws.send_json(frame)
                else:
                    chat = messages
                    if symbol:
                        chat = [{"role": "system", "content": (
                            f"The user is viewing {symbol} in a financial terminal. Be concise.")}, *messages]
                    async for tok in asst.stream_chat(eng.llm_base_url, model, chat, api_key=eng.llm_api_key):
                        await ws.send_json({"type": "token", "text": tok})
                    await ws.send_json({"type": "done"})
            except Exception as e:  # noqa: BLE001 - report stream errors to the client, keep socket open
                await ws.send_json({"type": "error", "detail": f"{type(e).__name__}"})
    except WebSocketDisconnect:
        return


async def _run_control_loop(ws, ctx, messages, symbol, workspace, capabilities, eng) -> None:
    """Drive the ReAct control generator, bouncing each `action` frame through the client for its
    observation (the mid-stream round-trip no other socket here does). Server data tools resolve
    inline, so only `action` frames await the client."""
    gen = agent.arun_react(
        ctx, messages, symbol, workspace, capabilities, eng.llm_base_url, eng.llm_api_key)
    try:
        frame = await gen.asend(None)
        while True:
            await ws.send_json(frame)
            if frame.get("type") == "action":
                try:
                    obs = await asyncio.wait_for(ws.receive_json(), timeout=25)
                except asyncio.TimeoutError:
                    obs = {"ok": False, "result": "client did not respond in time"}
                frame = await gen.asend(obs)
            else:
                frame = await gen.asend(None)
    except StopAsyncIteration:
        pass
    finally:
        await gen.aclose()
