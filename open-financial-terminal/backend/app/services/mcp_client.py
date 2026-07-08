"""Client for external MCP servers the grounded assistant consumes.

Discovers the tools exposed by the user-configured MCP servers (Settings → MCP Servers, persisted via
``config.get_mcp_servers``) and lets the assistant call them mid-answer alongside its native tools.

Tools are namespaced ``mcp:<server>:<tool>`` so they never collide with the seven built-in tools.
Everything here is best-effort and defensive: a down, slow, or misconfigured server yields an empty
tool list / an error string and NEVER breaks the chat (mirrors the assistant agent's planner).

Sessions are short-lived (opened per operation). stdio servers spawn a subprocess per call — simple
and safe; a persistent-session manager would cut that overhead and is a natural future optimization.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from app.config import get_mcp_servers

#: Tool-list cache so chat planning doesn't reconnect to every server on each message.
_CACHE_TTL = 60.0
_cache: dict[str, object] = {"at": 0.0, "tools": []}

PREFIX = "mcp:"
_OP_TIMEOUT = 20.0  # per server connect+list / connect+call; keeps a hung server from stalling chat.


def qualified_name(server: str, tool: str) -> str:
    return f"{PREFIX}{server}:{tool}"


def split_name(qualified: str) -> tuple[str, str] | None:
    """``mcp:<server>:<tool>`` → (server, tool), or None if it isn't a qualified MCP tool name."""
    if not qualified.startswith(PREFIX):
        return None
    rest = qualified[len(PREFIX):]
    server, sep, tool = rest.partition(":")
    if not sep or not server or not tool:
        return None
    return server, tool


@asynccontextmanager
async def _session(server: dict):
    """Open a ClientSession to one configured server (stdio or streamable-http) and initialize it."""
    if server["transport"] == "http":
        async with streamablehttp_client(server["url"], headers=server.get("headers") or None) as (
            read, write, _
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:  # stdio
        params = StdioServerParameters(
            command=server["command"],
            args=server.get("args") or [],
            # Merge onto the real environment so the child still finds PATH/APPDATA etc. on Windows.
            env={**os.environ, **(server.get("env") or {})},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def _list_one(server: dict) -> list[dict]:
    """List one server's tools as qualified-tool dicts. Any failure → [] (never raises)."""
    try:
        async with _session(server) as session:
            res = await asyncio.wait_for(session.list_tools(), timeout=_OP_TIMEOUT)
            return [
                {
                    "name": qualified_name(server["name"], t.name),
                    "server": server["name"],
                    "tool": t.name,
                    "description": t.description or "",
                    "input_schema": getattr(t, "inputSchema", None),
                }
                for t in res.tools
            ]
    except Exception:  # noqa: BLE001 - a bad server must not break discovery
        return []


async def list_external_tools(force: bool = False) -> list[dict]:
    """All enabled external MCP tools (qualified names + descriptions), cached with a short TTL."""
    now = time.monotonic()
    if not force and (now - float(_cache["at"])) < _CACHE_TTL:
        return list(_cache["tools"])  # type: ignore[arg-type]
    servers = [s for s in get_mcp_servers() if s.get("enabled", True)]
    tools: list[dict] = []
    if servers:
        results = await asyncio.gather(*(_list_one(s) for s in servers))
        for r in results:
            tools.extend(r)
    _cache["at"], _cache["tools"] = now, tools
    return list(tools)


def _flatten(result) -> tuple[str, dict]:  # noqa: ANN001
    """A CallToolResult → (compact text, structured dict), matching native tools' (text, data)."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    text = "\n".join(parts).strip() or "(no result)"
    data = getattr(result, "structuredContent", None)
    if getattr(result, "isError", False):
        text = f"(tool error) {text}"
    return text, (data if isinstance(data, dict) else {})


async def call_external_tool(qualified: str, arguments: dict | None) -> tuple[str, dict]:
    """Call ``mcp:<server>:<tool>`` with ``arguments``. Returns (text, data); never raises."""
    parsed = split_name(qualified)
    if not parsed:
        return f"(not an MCP tool: '{qualified}')", {}
    server_name, tool_name = parsed
    server = next((s for s in get_mcp_servers() if s["name"] == server_name and s.get("enabled", True)), None)
    if not server:
        return f"(MCP server '{server_name}' is not configured/enabled)", {}
    try:
        async with _session(server) as session:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments or {}), timeout=_OP_TIMEOUT
            )
            return _flatten(result)
    except Exception as e:  # noqa: BLE001 - a flaky external tool shouldn't kill the chat
        return f"(mcp:{server_name}:{tool_name} failed: {type(e).__name__})", {}


async def probe_server(server: dict) -> list[dict]:
    """List one server's tools (for the Settings 'test connection' button). Never raises."""
    return await _list_one(server)


def clear_cache() -> None:
    """Drop the tool-list cache (call after saving a changed server list)."""
    _cache["at"], _cache["tools"] = 0.0, []
