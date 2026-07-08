"""External-MCP client + config: name helpers, result flattening, persistence, and a fake-session
happy path. No real subprocess/network — the session is monkeypatched."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import config
from app.services import mcp_client


# ── name helpers ────────────────────────────────────────────────────────────────────
def test_qualified_and_split_round_trip():
    q = mcp_client.qualified_name("weather", "forecast")
    assert q == "mcp:weather:forecast"
    assert mcp_client.split_name(q) == ("weather", "forecast")


def test_split_rejects_non_mcp_and_malformed():
    assert mcp_client.split_name("get_quote") is None       # native tool, no prefix
    assert mcp_client.split_name("mcp:onlyserver") is None   # missing :tool


# ── result flattening ─────────────────────────────────────────────────────────────────
def test_flatten_joins_text_blocks_and_keeps_structured():
    result = SimpleNamespace(
        content=[SimpleNamespace(text="line 1"), SimpleNamespace(text="line 2")],
        structuredContent={"value": 42},
        isError=False,
    )
    text, data = mcp_client._flatten(result)
    assert text == "line 1\nline 2" and data == {"value": 42}


def test_flatten_marks_errors():
    result = SimpleNamespace(content=[SimpleNamespace(text="boom")], structuredContent=None, isError=True)
    text, data = mcp_client._flatten(result)
    assert text.startswith("(tool error)") and data == {}


# ── config persistence ────────────────────────────────────────────────────────────────
@pytest.fixture()
def clean_mcp():
    path = config._mcp_servers_path()
    backup = path.read_bytes() if path.exists() else None
    path.unlink(missing_ok=True)
    try:
        yield path
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(backup)


def test_config_round_trip_validates_and_dedupes(clean_mcp):
    saved = config.set_mcp_servers([
        {"name": "echo", "transport": "stdio", "command": "python", "args": ["-m", "echo"]},
        {"name": "remote", "transport": "http", "url": "https://example.com/mcp"},
        {"name": "echo", "command": "python"},        # duplicate name → suffixed
        {"name": "bad-stdio"},                          # no command, no url → dropped
        {"name": "bad-http", "transport": "http", "url": "ftp://nope"},  # bad scheme → dropped
    ])
    names = [s["name"] for s in saved]
    assert names == ["echo", "remote", "echo-2"]
    assert config.get_mcp_servers()[0]["args"] == ["-m", "echo"]
    # transport is inferred when omitted (url present → http)
    assert config.get_mcp_servers()[1]["transport"] == "http"


# ── discovery / dispatch ───────────────────────────────────────────────────────────────
def test_list_external_tools_empty_when_none_configured(monkeypatch):
    monkeypatch.setattr(mcp_client, "get_mcp_servers", lambda: [])
    assert asyncio.run(mcp_client.list_external_tools(force=True)) == []


def test_call_unconfigured_server_is_graceful(monkeypatch):
    monkeypatch.setattr(mcp_client, "get_mcp_servers", lambda: [])
    text, data = asyncio.run(mcp_client.call_external_tool("mcp:ghost:do", {}))
    assert "not configured" in text and data == {}


def test_list_and_call_with_fake_session(monkeypatch):
    server = {"name": "fake", "transport": "stdio", "command": "x", "args": [], "env": {},
              "url": "", "headers": {}, "enabled": True}
    monkeypatch.setattr(mcp_client, "get_mcp_servers", lambda: [server])

    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[
                SimpleNamespace(name="forecast", description="get a forecast", inputSchema={"type": "object"})
            ])

        async def call_tool(self, name, arguments):
            assert name == "forecast" and arguments == {"city": "NYC"}
            return SimpleNamespace(content=[SimpleNamespace(text="sunny")], structuredContent=None, isError=False)

    @asynccontextmanager
    async def fake_session(_server):
        yield FakeSession()

    monkeypatch.setattr(mcp_client, "_session", fake_session)

    listed = asyncio.run(mcp_client.list_external_tools(force=True))
    assert listed[0]["name"] == "mcp:fake:forecast" and listed[0]["tool"] == "forecast"

    text, _ = asyncio.run(mcp_client.call_external_tool("mcp:fake:forecast", {"city": "NYC"}))
    assert text == "sunny"
