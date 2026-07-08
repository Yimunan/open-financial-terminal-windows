"""The read-only tool-runner endpoint that the standalone MCP server wraps.

No network: the tool dispatcher is monkeypatched so we exercise the routing/validation, not a live
yfinance/qhfi fetch.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import assistant_tools as tools


def test_catalog_lists_all_tools():
    client = TestClient(app)
    r = client.get("/api/assistant/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert names == set(tools.TOOLS)
    # every entry carries the description + arg hint the MCP server turns into a tool schema
    assert all(t["desc"] and t["args"] for t in r.json()["tools"])


def test_unknown_tool_is_404():
    client = TestClient(app)
    r = client.post("/api/assistant/tools/nope", json={})
    assert r.status_code == 404


def test_run_tool_reuses_dispatcher(monkeypatch):
    calls = {}

    def fake_run(name, ctx, args):
        calls["name"], calls["args"] = name, args
        return "AAPL price: 200.0 (+1.0% on the day) — close.", {"symbol": "AAPL", "price": 200.0}

    # the router calls tools.run_tool by module attribute, so patching here intercepts it
    monkeypatch.setattr(tools, "run_tool", fake_run)
    client = TestClient(app)
    r = client.post("/api/assistant/tools/get_quote", json={"symbol": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"].startswith("AAPL price")
    assert body["data"]["price"] == 200.0
    # the path name + JSON body were forwarded verbatim to the dispatcher
    assert calls["name"] == "get_quote" and calls["args"]["symbol"] == "AAPL"
