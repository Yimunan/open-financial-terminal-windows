"""Open Financial Terminal — standalone MCP server (stdio).

Exposes the terminal's seven read-only data tools (quote, performance, fundamentals, news, screen,
compare, search) over the Model Context Protocol so Claude Code / Claude Desktop / OpenCode and other
agents can query live market data.

It is deliberately thin and dependency-light (just ``mcp`` + ``httpx``, no qhfi): each tool POSTs to
the running terminal backend's ``/api/assistant/tools/{name}`` endpoint, which runs the *exact* same
tool functions the in-app assistant uses. So this process starts fast (important for a Claude-spawned
subprocess) and never duplicates any symbol-resolution / return-computation logic.

Run it:
    python -m mcp_server.server            # from the backend/ directory, with the venv python

Config:
    OFT_MCP_BASE_URL   terminal backend base URL (default http://localhost:8050)

The backend has no auth (localhost/CORS only); this server inherits that trust model — intended for
local use.
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("OFT_MCP_BASE_URL", "http://localhost:8050").rstrip("/")
_TIMEOUT = 30.0  # qhfi's first fetch of a symbol can refresh bars/fundamentals from the network.

mcp = FastMCP("open-financial-terminal")


def _call(name: str, **args: object) -> str:
    """POST a tool call to the terminal backend and return its grounded text.

    Drops ``None`` args so backend defaults apply. Network/HTTP failures come back as a readable
    string (rather than raising) so the calling agent can react instead of seeing a transport error.
    """
    payload = {k: v for k, v in args.items() if v is not None}
    try:
        r = httpx.post(f"{BASE_URL}/api/assistant/tools/{name}", json=payload, timeout=_TIMEOUT)
        if r.status_code == 404:
            return f"(unknown tool '{name}')"
        r.raise_for_status()
        return str(r.json().get("text") or "(no result)")
    except httpx.ConnectError:
        return (
            f"Cannot reach the Open Financial Terminal backend at {BASE_URL}. "
            "Is it running (uvicorn app.main:app --port 8050)?"
        )
    except Exception as e:  # noqa: BLE001 - report transport issues as text, never crash the tool
        return f"({name} failed: {type(e).__name__})"


@mcp.tool()
def get_quote(symbol: str, asset: str | None = None) -> str:
    """Latest price and day change for a symbol (equity EOD or crypto live-ish).

    asset: "equity" or "crypto" (inferred from the symbol when omitted; e.g. BTC/USDT → crypto).
    """
    return _call("get_quote", symbol=symbol, asset=asset)


@mcp.tool()
def get_performance(
    symbol: str, period: str | None = None, asset: str | None = None,
    start: str | None = None, end: str | None = None,
) -> str:
    """Total price return over a trailing period (1w/2w/1m/3m/6m/1y/ytd) for a symbol.

    For a specific historical window pass ISO ``start``/``end`` dates instead of ``period``.
    """
    return _call("get_performance", symbol=symbol, period=period, asset=asset, start=start, end=end)


@mcp.tool()
def get_fundamentals(symbol: str) -> str:
    """Valuation/quality snapshot: market cap, P/E, P/B, EPS, beta, ROE, dividend, 52w range (equities only)."""
    return _call("get_fundamentals", symbol=symbol)


@mcp.tool()
def get_news(symbol: str, limit: int | None = None) -> str:
    """Recent ranked headlines with sentiment for a symbol (limit 1-12, default 6)."""
    return _call("get_news", symbol=symbol, limit=limit)


@mcp.tool()
def screen(factor: str | None = None, universe: str | None = None, limit: int | None = None) -> str:
    """Rank a universe by a factor and return the top names.

    factor: e.g. momentum, value, quality, volatility, reversal (defaults to momentum).
    universe: a terminal universe, e.g. dow30, sp500, nasdaq100 (defaults to dow30).
    """
    return _call("screen", factor=factor, universe=universe, limit=limit)


@mcp.tool()
def compare(
    symbols: list[str], period: str | None = None,
    start: str | None = None, end: str | None = None,
) -> str:
    """Compare price performance of 2-6 symbols over a trailing period or an ISO start/end window."""
    return _call("compare", symbols=symbols, period=period, start=start, end=end)


@mcp.tool()
def search_symbols(query: str) -> str:
    """Resolve a company name or ticker fragment to real symbols in the terminal's universes."""
    return _call("search_symbols", query=query)


if __name__ == "__main__":
    mcp.run()
