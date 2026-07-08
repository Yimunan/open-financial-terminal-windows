"""Grounded assistant agent.

A two-phase pipeline that turns the naked chat into a data-grounded one:

1. **Plan** — one schema-constrained LLM call (``llm.structured``, the reliable JSON path used by
   the screener) decides which read-only data tools to run for the user's latest message. For pure
   conceptual questions it returns an empty plan.
2. **Answer** — the planned tools are executed against the real services and their compact results
   are injected as a DATA block; the final reply is then *streamed* token-by-token with strict
   rules: ground every fact in DATA, never invent prices/P-E/news.

Yields frames ``{type: 'tool'|'token'|'done'|'error'}`` so the widget can show what data was
fetched and stream the prose answer.
"""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import AsyncIterator
from datetime import date

from app.services import _jsonutil as stj
from app.services import assistant as asst
from app.services import assistant_tools as tools
from app.services import mcp_client
from app.services.assistant_tools import ToolContext

_MAX_TOOLS = 5


def _build_plan_schema(ext_names: list[str]) -> dict:
    """Plan schema whose ``name`` enum spans the native tools + any external MCP tools.

    Native tools keep their flat typed args; external MCP tools carry their parameters in the generic
    ``arguments`` object (their schemas are arbitrary, so we don't enumerate them here)."""
    return {
        "type": "object",
        "properties": {
            "tools": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": list(tools.TOOLS) + ext_names},
                        "symbol": {"type": "string"},
                        "asset": {"type": "string", "enum": ["equity", "crypto"]},
                        "period": {"type": "string"},
                        "universe": {"type": "string"},
                        "factor": {"type": "string"},
                        "symbols": {"type": "array", "items": {"type": "string"}},
                        "query": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "limit": {"type": "integer"},
                        # External MCP tools pass their own parameters here.
                        "arguments": {"type": "object"},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["tools"],
    }


def _plan_system(symbol: str | None, ext_tools: list[dict] | None = None) -> str:
    catalog = "\n".join(f"- {n}({m['args']}): {m['desc']}" for n, m in tools.TOOLS.items())
    ext_block = ""
    if ext_tools:
        ext_lines = "\n".join(f"- {t['name']}: {t['description']}" for t in ext_tools)
        ext_block = (
            "\nExternal MCP tools (put their parameters in `arguments` as a JSON object):\n"
            f"{ext_lines}\n"
        )
    return (
        f"You plan data lookups for a financial-terminal assistant. Today is {date.today()}. "
        f"The user is currently viewing {symbol or 'no specific symbol'}.\n"
        "Given the user's latest message, choose the read-only tools whose data you need to "
        "answer it factually, and the arguments for each. Available tools:\n"
        f"{catalog}\n"
        f"{ext_block}\n"
        "Rules:\n"
        "- Default any missing symbol to the symbol the user is viewing.\n"
        "- For greetings, capability questions, or general finance/quant concept questions that "
        "need no live data, return an EMPTY tools list.\n"
        "- Do NOT call more tools than needed. Prefer 0-3 tools.\n"
        "- Use get_quote for 'price now' (also gives the day's high/low and volume), "
        "get_performance for 'how has it done', "
        "get_fundamentals for valuation/P-E/market cap, get_news for headlines/why-moving, "
        "screen for 'top/best X in a universe', compare for 'X vs Y', search_symbols to look up a "
        "symbol from a company name/sector you're unsure of.\n"
        "- You already KNOW common tickers (JPMorgan=JPM, Apple=AAPL); don't call search_symbols "
        "just to map a well-known company name — only when the symbol is genuinely unknown.\n"
        "ARGUMENT RULES (important):\n"
        "- screen: set `factor` (e.g. momentum, value, quality, volatility) and `universe` (e.g. "
        "dow30, sp500, nasdaq100). Do NOT put these in `symbols`.\n"
        "- compare: set `symbols` to a JSON array of tickers, e.g. [\"AAPL\",\"MSFT\"]. Do NOT pack "
        "them into one comma string.\n"
        "- get_quote/get_performance/get_fundamentals/get_news: set `symbol` to ONE ticker.\n"
        f"- get_performance: for a trailing span use `period` (1w/2w/1m/3m/6m/1y/ytd); for a specific "
        f"historical window (e.g. 'between March and May 2026') set `start` and `end` as ISO dates "
        f"(YYYY-MM-DD). Today is {date.today()}."
    )


def _answer_system(symbol: str | None, observations: list[str], capability: bool) -> str:
    data = "\n\n".join(observations) if observations else "(no data was fetched for this question)"
    extra = ("\nCAPABILITIES (use this verbatim if the user asks what you can do):\n"
             + tools.capabilities_text()) if capability else ""
    return (
        f"You are the assistant inside Open Financial Terminal. Today is {date.today()}. "
        f"The user is viewing {symbol or 'no specific symbol'}. Be concise, practical, and use "
        "compact markdown. This is an internal terminal, not investment advice — no boilerplate "
        "disclaimers.\n"
        "GROUNDING RULES (critical):\n"
        "- LIVE/QUANTITATIVE facts — prices, percentage moves, market cap, P/E and other valuations, "
        "current rankings, and recent news/headlines — must come ONLY from the DATA block below. "
        "NEVER invent or estimate these, and never claim real-time data you weren't given.\n"
        "- STABLE facts you may answer from your own knowledge: a company's ticker symbol, what a "
        "company does, its sector/industry, and general finance/quant concepts. So 'what's the "
        "ticker for JPMorgan?' → answer JPM directly.\n"
        "- If the DATA lacks a live number the user asked for, say you don't have it rather than "
        "guessing. Equity prices are EOD/delayed; say so if precision matters.\n"
        "- If the request is genuinely ambiguous (e.g. 'the bank' with no company named), ask one "
        "short clarifying question instead of guessing.\n"
        "NEUTRALITY & TONE:\n"
        "- Stay objective and neutral. Report the data and, where a view is asked for, present a "
        "BALANCED picture (both supportive and opposing factors) rather than taking a side.\n"
        "- Do NOT give buy/sell/hold recommendations, price targets, or directional predictions "
        "('it will go up'). If asked, briefly note you don't give investment advice, then give the "
        "neutral data the user can decide from.\n"
        "- Avoid hype, promotional or emotionally loaded language (no 'soaring', 'crushing it', "
        "'must-buy'); describe moves factually (e.g. 'up 3%').\n"
        f"{extra}\n\n"
        f"DATA:\n{data}"
    )


def _plan(
    ctx: ToolContext, messages: list[dict], symbol: str | None, ext_tools: list[dict] | None = None
) -> list[dict]:
    """One structured call → list of tool specs. Defensive: any failure → no tools (plain chat)."""
    ext_tools = ext_tools or []
    ext_names = [t["name"] for t in ext_tools]
    valid = set(tools.TOOLS) | set(ext_names)
    user_msgs = [m for m in messages if m.get("role") == "user"]
    last = user_msgs[-1]["content"] if user_msgs else ""
    # Give the planner a little prior context for follow-ups ("and its P/E?").
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages[-5:])
    try:
        schema = _build_plan_schema(ext_names)
        out = ctx.llm.structured(_plan_system(symbol, ext_tools), convo or last, schema, model=ctx.model)
        plan = out.get("tools", []) if isinstance(out, dict) else []
        return [t for t in plan if isinstance(t, dict) and t.get("name") in valid][:_MAX_TOOLS]
    except Exception:  # noqa: BLE001 - planning is best-effort; fall back to ungrounded chat
        return []


def _wants_capabilities(messages: list[dict]) -> bool:
    last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "").lower()
    keys = ("what can you do", "what do you do", "your capabilities", "what are you connected",
            "what can you help", "how can you help", "what tools")
    return any(k in last for k in keys)


async def run(
    ctx: ToolContext, messages: list[dict], symbol: str | None, base_url: str, api_key: str | None
) -> AsyncIterator[dict]:
    """Plan → fetch → stream a grounded answer. Yields tool/token/done/error frames."""
    ctx.symbol = symbol
    # Discover external MCP tools (cached, best-effort: returns [] if none configured or all down,
    # so with no servers the loop is byte-for-byte the original native-only behavior).
    ext_tools = await mcp_client.list_external_tools()
    # The planner + native tools call qhfi/yfinance/the LLM synchronously; run them off the event
    # loop so the WebSocket keepalive ping is still answered (else slow fetches kill the socket).
    plan = await asyncio.to_thread(_plan, ctx, messages, symbol, ext_tools)

    observations: list[str] = []
    for spec in plan:
        name = spec["name"]
        if name.startswith(mcp_client.PREFIX):  # external MCP tool — args live in `arguments`
            args = spec.get("arguments") or {}
            text, data = await mcp_client.call_external_tool(name, args)
        else:  # native tool — flat args, run off the event loop
            args = {k: v for k, v in spec.items() if k not in ("name", "arguments")}
            # Gemma (and other models) often nest a native tool's args under the generic
            # `arguments` object the schema also offers for MCP tools; fold those in so a
            # by-name fetch (symbol/factor/universe) isn't silently dropped. Flat keys win.
            if isinstance(spec.get("arguments"), dict):
                args = {**spec["arguments"], **args}
            text, data = await asyncio.to_thread(tools.run_tool, name, ctx, args)
        observations.append(text)
        yield {"type": "tool", "name": name, "args": {k: v for k, v in args.items() if v is not None},
               "summary": (data or {})}

    capability = _wants_capabilities(messages)
    sys = _answer_system(symbol, observations, capability)
    # Strip any pre-existing system message from the client; we own grounding now.
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    chat = [{"role": "system", "content": sys}, *convo]

    try:
        async for tok in asst.stream_chat(base_url, ctx.model, chat, api_key=api_key):
            yield {"type": "token", "text": tok}
        yield {"type": "done"}
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "detail": f"{type(e).__name__}"}


# ── ReAct control loop: perceive the workspace + act on the modules ──────────────────────────────
#
# A bounded act-observe loop that, in addition to the read-only DATA tools above, can DRIVE the
# terminal's modules. Each turn the model emits ONE JSON action: a server data tool (run inline), a
# CLIENT action (open/configure/link a widget, switch workspace — executed in the browser via an
# `action` frame whose observation is fed back through the socket), or {"answer": true} to finish.
# When it answers, the reply streams grounded in everything fetched + done, exactly like run().

_MAX_STEPS = 8

#: The navigate/configure action verbs the assistant may drive. Mutating verbs (orders, deletes,
#: model/watchlist writes) are deliberately absent — the client dispatcher also allowlists these.
CLIENT_ACTIONS = (
    "open_widget", "set_symbol", "configure_widget", "switch_workspace", "apply_template",
    "read_workspace",
)


def _fmt_workspace(workspace: dict | None) -> str:
    """One compact line per open panel + the channel symbols, so the model knows what's on screen
    and can target a panel by id."""
    if not workspace:
        return "(workspace state not provided)"
    panels = workspace.get("panels") or []
    chans = workspace.get("channels") or {}
    lines = []
    if panels:
        lines.append("Open panels (id · type · key params):")
        for p in panels[:40]:
            params = p.get("params") or {}
            keep = {k: v for k, v in params.items() if k in (
                "symbol", "asset", "timeframe", "indicators", "chartType", "channel", "category",
                "btMode", "initialQuery") and v not in (None, "", [])}
            lines.append(f"- {p.get('id')} · {p.get('type')} · {keep or '—'}")
    else:
        lines.append("No panels are open.")
    if chans:
        lines.append("Linked channel symbols: " + ", ".join(
            f"{c}={(v or {}).get('symbol')}" for c, v in chans.items()))
    for key, label in (("current", "Active workspace"), ("names", "Workspaces"), ("templates", "Templates")):
        if workspace.get(key):
            lines.append(f"{label}: {workspace[key]}")
    return "\n".join(lines)


def _fmt_widget_catalog(capabilities: dict | None) -> str:
    """The registry-derived 'what each module is for + accepts' catalog the client sent."""
    widgets = (capabilities or {}).get("widgets") or []
    if not widgets:
        return "(no widget catalog provided)"
    lines = ["Widget types you can open/configure (type — purpose — params):"]
    for w in widgets:
        params = w.get("params") or {}
        ps = ", ".join(f"{k} ({v})" for k, v in params.items()) if params else "no params"
        lines.append(f"- {w.get('type')} — {w.get('description', '')} — {ps}")
    return "\n".join(lines)


def _react_system(symbol: str | None, workspace: dict | None, capabilities: dict | None) -> str:
    catalog = "\n".join(f"- {n}({m['args']}): {m['desc']}" for n, m in tools.TOOLS.items())
    actions = [a for a in (capabilities or {}).get("actions", []) if a in CLIENT_ACTIONS]
    return (
        f"You are the agent inside Open Financial Terminal. Today is {date.today()}. The user is "
        f"viewing {symbol or 'no specific symbol'}.\n"
        "You work step by step: each turn you choose ONE action, observe its result, then decide the "
        "next — until the user's request is satisfied, then you answer.\n\n"
        "DATA TOOLS (read-only; the result comes back as an observation):\n"
        f"{catalog}\n\n"
        "TERMINAL ACTIONS (drive the UI; a short confirmation comes back as an observation):\n"
        f"- open_widget(type, params): open a module, optionally pre-configured. {', '.join(actions)} are available.\n"
        "- set_symbol(channel, symbol, asset): retarget a link channel (red/blue/green); all widgets "
        "on that channel follow. asset is equity or crypto.\n"
        "- configure_widget(id, params): change an OPEN panel's params (e.g. timeframe, indicators, "
        "channel). Use a panel id from the workspace state.\n"
        "- switch_workspace(name) / apply_template(name): change the layout.\n"
        "- read_workspace(): re-read what's on screen after acting.\n\n"
        f"{_fmt_widget_catalog(capabilities)}\n\n"
        "CURRENT WORKSPACE STATE:\n"
        f"{_fmt_workspace(workspace)}\n\n"
        "RULES:\n"
        "- Prefer opening a pre-configured widget over multiple steps (e.g. open_widget chart with "
        "{symbol, timeframe, indicators}). Many widgets auto-run from their params.\n"
        "- Default any missing symbol to the one the user is viewing. Don't re-fetch data you already "
        "have. Use as FEW steps as possible (ideally 0-3 actions).\n"
        "- You may ONLY navigate/configure — never place orders or delete things.\n"
        "- Reply with ONE JSON object and nothing else: {\"thought\": \"...\", \"tool\": \"<name>\", "
        "\"args\": {...}}. When the request is fully handled (or it's a pure question needing no UI "
        "change), reply {\"answer\": true} instead and you'll then write the final response."
    )


def _react_user(messages: list[dict], history: list[tuple[str, str]]) -> str:
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages[-6:] if m.get("role") in ("user", "assistant"))
    steps = "\n".join(f"action: {a}\nobservation: {o}" for a, o in history)
    return (f"Conversation:\n{convo}\n\n"
            + (f"Steps so far:\n{steps}\n\n" if steps else "")
            + "Choose the next action, or {\"answer\": true} if done.")


def _react_answer_system(symbol: str | None, fetched: list[str], actions_log: list[str], capability: bool) -> str:
    base = _answer_system(symbol, fetched, capability)
    if actions_log:
        base += "\n\nACTIONS YOU PERFORMED (tell the user briefly what you did):\n" + "\n".join(
            f"- {a}" for a in actions_log)
    return base


def _obs_text(client_obs: object) -> str:
    """Normalize the client's observation reply ({ok, result} | str | None) into a short string."""
    if isinstance(client_obs, dict):
        res = client_obs.get("result")
        ok = client_obs.get("ok", True)
        body = res if isinstance(res, str) else json.dumps(res, default=str)[:400] if res is not None else ""
        return ("ok" if ok else "failed") + (f": {body}" if body else "")
    if isinstance(client_obs, str):
        return client_obs
    return "ok"


async def arun_react(
    ctx: ToolContext, messages: list[dict], symbol: str | None,
    workspace: dict | None, capabilities: dict | None, base_url: str, api_key: str | None,
) -> AsyncIterator[dict]:
    """Plan→act→observe loop that can fetch data AND drive the terminal's modules, then stream a
    grounded answer. Client actions round-trip: ``client_obs = yield {type:'action', ...}`` — the
    route sends the frame, executes it in the browser, and resumes the loop with the observation."""
    ctx.symbol = symbol
    allowed = set(a for a in (capabilities or {}).get("actions", []) if a in CLIENT_ACTIONS)
    history: list[tuple[str, str]] = []
    fetched: list[str] = []        # DATA tool text, for grounding the final answer
    actions_log: list[str] = []    # UI actions performed, summarized for the final answer

    try:
        for _ in range(_MAX_STEPS):
            sysp = _react_system(symbol, workspace, capabilities)
            user = _react_user(messages, history)
            raw = await asyncio.to_thread(
                functools.partial(ctx.llm.complete, sysp, user, model=ctx.model, temperature=0.2))
            act = stj.strip_to_json(raw) or {"answer": True}
            if act.get("answer") or act.get("done") or not act.get("tool"):
                break

            thought = str(act.get("thought", "")).strip()[:300]
            if thought:
                yield {"type": "thought", "text": thought}
            name = str(act.get("tool"))
            args = act.get("args") if isinstance(act.get("args"), dict) else {}

            if name in tools.TOOLS:
                text, data = await asyncio.to_thread(tools.run_tool, name, ctx, args)
                fetched.append(text)
                yield {"type": "tool", "name": name,
                       "args": {k: v for k, v in args.items() if v is not None}, "summary": (data or {})}
                obs = text
            elif name in allowed:
                aid = f"a{len(history) + 1}"
                client_obs = yield {"type": "action", "id": aid, "name": name, "args": args}
                obs = _obs_text(client_obs)
                actions_log.append(f"{name} {json.dumps(args, default=str)[:120]} → {obs[:120]}")
                # read_workspace (or any action) may return a refreshed snapshot to plan against.
                if isinstance(client_obs, dict) and isinstance(client_obs.get("result"), dict):
                    snap = client_obs["result"].get("workspace")
                    if isinstance(snap, dict):
                        workspace = snap
            else:
                obs = f"unknown tool '{name}'"
                yield {"type": "obs", "text": obs}

            history.append((json.dumps(act, default=str)[:200], obs))
    except Exception as e:  # noqa: BLE001 — never let a planning/action error kill the socket
        yield {"type": "error", "detail": f"{type(e).__name__}"}
        return

    capability = _wants_capabilities(messages)
    sysp = _react_answer_system(symbol, fetched, actions_log, capability)
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    chat = [{"role": "system", "content": sysp}, *convo]
    try:
        async for tok in asst.stream_chat(base_url, ctx.model, chat, api_key=api_key):
            yield {"type": "token", "text": tok}
        yield {"type": "done"}
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "detail": f"{type(e).__name__}"}
