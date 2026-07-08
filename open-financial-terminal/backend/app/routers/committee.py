"""Investment Committee — proxy + stream relay to the standalone crewai-service (:8083).

The terminal keeps CrewAI (and its heavy deps) out of process: this router forwards Knowledge-Base
CRUD to the crewai-service and relays its deliberation SSE stream over a WebSocket so the widget can
render the committee debating live, then a structured verdict.

When the crewai-service is unreachable (it's an often-offline external process — e.g. the WSL host
isn't up), the convene WebSocket falls back to a *local* LLM committee (``committee_local``) and
streams it in the same frame shape, so the Investment Committee module still produces a real
deliberation with no external dependency. This mirrors the agent-workflow committee node, which
already degrades to the same local committee.
"""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, WebSocket, WebSocketDisconnect

from app.config import get_terminal_settings
from app.deps import get_store
from app.store import TerminalStore

router = APIRouter(prefix="/api/committee", tags=["committee"])

#: Default roster surfaced to the UI for prefill (the crew uses the same fallback server-side).
#: The last member is the Chair / synthesizer.
DEFAULT_ROSTER: list[dict] = [
    {
        "role": "Bull / Growth Analyst",
        "goal": "Make the strongest evidence-based case FOR the investment",
        "backstory": "A growth-oriented analyst who hunts for asymmetric upside, durable moats, and underappreciated catalysts.",
    },
    {
        "role": "Bear / Risk Analyst",
        "goal": "Make the strongest evidence-based case AGAINST the investment and surface key risks",
        "backstory": "A skeptical risk manager who stress-tests theses, looks for crowded trades, valuation traps, and downside scenarios.",
    },
    {
        "role": "Macro Strategist",
        "goal": "Place the investment in its macro, sector, and cycle context",
        "backstory": "A top-down strategist who weighs rates, liquidity, sector rotation, and regime risk against the bottom-up thesis.",
    },
    {
        "role": "Chair (Chief Investment Officer)",
        "goal": "Weigh the committee's arguments and issue a single, decisive recommendation",
        "backstory": "A seasoned CIO who balances conviction with risk discipline and is accountable for the final call and position sizing.",
    },
]

#: A risk-focused committee. The Chair (CRO) is the last member / synthesizer.
RISK_ROSTER: list[dict] = [
    {
        "role": "Market Risk Analyst",
        "goal": "Assess market risk — volatility, VaR, factor exposures, and tail scenarios",
        "backstory": "A market-risk specialist who quantifies drawdown, volatility regimes, factor crowding, beta, and tail risk under stress.",
    },
    {
        "role": "Credit Risk Analyst",
        "goal": "Assess credit and counterparty risk",
        "backstory": "A credit-risk specialist focused on default probability, spread widening, rating downgrades, and counterparty/issuer exposure.",
    },
    {
        "role": "Liquidity Risk Analyst",
        "goal": "Assess funding and market liquidity risk",
        "backstory": "A liquidity specialist who evaluates position liquidity, funding stability, crowding, and exit/slippage costs under stress.",
    },
    {
        "role": "Chief Risk Officer",
        "goal": "Weigh the risk assessments and issue the committee's risk verdict and exposure guidance",
        "backstory": "A seasoned CRO accountable for the firm's risk posture, balancing return objectives against drawdown and tail-risk discipline.",
    },
]

#: Built-in committee presets offered when creating a new committee (name → roster).
PRESETS: list[dict] = [
    {"name": "Investment Committee", "members": DEFAULT_ROSTER},
    {"name": "Risk Committee", "members": RISK_ROSTER},
]


def _base() -> str:
    return get_terminal_settings().committee_base_url.rstrip("/")


async def _forward(method: str, path: str, **kw):
    """Proxy a JSON request to the crewai-service, surfacing its status/detail."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.request(method, f"{_base()}{path}", **kw)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"crewai-service unreachable: {type(e).__name__}") from None
    if r.status_code >= 400:
        detail = r.json().get("detail") if "application/json" in r.headers.get("content-type", "") else r.text
        raise HTTPException(r.status_code, detail or "crewai-service error")
    return r.json()


@router.get("/roster")
def roster() -> dict:
    return {"members": DEFAULT_ROSTER}


@router.get("/presets")
def presets() -> dict:
    """Built-in committee presets (Investment Committee, Risk Committee, …) for the create menu."""
    return {"presets": PRESETS}


# --- Committee templates (reusable named rosters, persisted in the terminal SQLite) --------------


@router.get("/templates")
def templates_list(store: TerminalStore = Depends(get_store)) -> dict:
    return {"templates": store.list_committee_templates()}


@router.put("/templates/{name}")
def template_save(name: str, body: dict, store: TerminalStore = Depends(get_store)) -> dict:
    name = name.strip()
    members = (body or {}).get("members") or []
    if not name:
        raise HTTPException(400, "template name is required")
    if len(members) < 2:
        raise HTTPException(400, "a committee needs at least one analyst plus a Chair")
    store.save_committee_template(name, {"name": name, "members": members})
    return {"ok": True, "name": name}


@router.delete("/templates/{name}")
def template_delete(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    store.remove_committee_template(name)
    return {"ok": True}


# --- Knowledge proxy: configurable directory + Directory/Committee/Agent file store ---------------


@router.get("/knowledge-dir")
async def knowledge_dir_get() -> dict:
    return await _forward("GET", "/knowledge-dir")


@router.put("/knowledge-dir")
async def knowledge_dir_set(body: dict) -> dict:
    return await _forward("PUT", "/knowledge-dir", json={"dir": (body or {}).get("dir", "")})


@router.get("/knowledge/tree")
async def knowledge_tree() -> dict:
    return await _forward("GET", "/knowledge/tree")


@router.get("/knowledge/files")
async def knowledge_files(committee: str, agent: str) -> dict:
    return await _forward("GET", "/knowledge/files", params={"committee": committee, "agent": agent})


@router.post("/knowledge/files")
async def knowledge_upload(committee: str, agent: str, file: UploadFile) -> dict:
    data = await file.read()
    files = {"file": (file.filename or "file", data, file.content_type or "application/octet-stream")}
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.post(
                f"{_base()}/knowledge/files", params={"committee": committee, "agent": agent}, files=files
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"crewai-service unreachable: {type(e).__name__}") from None
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json().get("detail", "upload failed"))
    return r.json()


@router.delete("/knowledge/files")
async def knowledge_delete_file(committee: str, agent: str, name: str) -> dict:
    return await _forward(
        "DELETE", "/knowledge/files", params={"committee": committee, "agent": agent, "name": name}
    )


# --- Convene (WebSocket SSE relay) ---------------------------------------------------------------


def _sse_events(raw: str):
    """Yield (event, data) pairs from a buffered SSE block."""
    event, data_lines = "message", []
    for line in raw.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return event, "\n".join(data_lines)


async def _local_convene(ws: WebSocket, inputs: dict) -> None:
    """Stream a local-LLM committee deliberation when the crewai-service is unavailable.

    Emits the SAME frames the SSE relay does (``task`` per analyst, ``result`` for the Chair, then
    ``done``), so the widget renders it identically to a real crewai run. Never raises: if the local
    LLM is also unusable it sends an ``error`` frame and closes the convene with ``done``.
    """
    from app.deps import get_llm_client, get_llm_model
    from app.services import committee_local as cl

    committee = inputs.get("committee") or "Investment Committee"
    members = inputs.get("members") or DEFAULT_ROSTER
    mandate = (inputs.get("prompt") or "").strip()
    if inputs.get("symbol"):
        mandate = f"Ticker under review: {inputs['symbol']}\n\n{mandate}".strip()
    try:
        llm, model = get_llm_client(), get_llm_model()
        async for event, payload in cl.stream_deliberation(llm, model, committee, members, mandate):
            await ws.send_json({"type": event, "payload": payload})
    except Exception as e:  # noqa: BLE001 - local LLM unusable → surface, don't crash the socket
        await ws.send_json({"type": "error", "detail": f"committee fallback failed: {type(e).__name__}"})
    await ws.send_json({"type": "done"})


@router.websocket("/ws")
async def ws_committee(ws: WebSocket) -> None:
    """Client sends {prompt, symbol?, members?, knowledge_id?}; server relays the crew's
    deliberation as {type:'step'|'task'|'result'|'error', ...} frames, ending each convene with done.
    """
    await ws.accept()
    try:
        while True:
            req = await ws.receive_json()
            inputs = {
                "prompt": req.get("prompt", ""),
                "symbol": req.get("symbol"),
                "committee": req.get("committee"),
                "members": req.get("members"),
                "edges": req.get("edges"),
            }
            payload = {"crew": "committee", "inputs": inputs}
            # Prefer the real crewai-service; if it's unreachable or errors before streaming anything,
            # fall back to a local LLM committee (same as the agent-workflow committee node). `relayed`
            # guards against replaying locally after the service already streamed partial output.
            relayed = False
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
                    async with client.stream("POST", f"{_base()}/run/stream", json=payload) as resp:
                        if resp.status_code >= 400:
                            await _local_convene(ws, inputs)  # service up but erroring → local fallback
                            continue
                        block: list[str] = []
                        async for line in resp.aiter_lines():
                            if line:
                                block.append(line)
                                continue
                            if not block:
                                continue
                            event, data = _sse_events("\n".join(block))
                            block = []
                            try:
                                obj = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            await ws.send_json({"type": event, "payload": obj.get("payload")})
                            relayed = True
                await ws.send_json({"type": "done"})
            except httpx.HTTPError as e:
                if relayed:
                    # Dropped mid-stream after partial output — don't replay the whole deliberation.
                    await ws.send_json({"type": "error", "detail": f"crewai-service dropped: {type(e).__name__}"})
                    await ws.send_json({"type": "done"})
                else:
                    await _local_convene(ws, inputs)  # never reached the service → local fallback
    except WebSocketDisconnect:
        return
