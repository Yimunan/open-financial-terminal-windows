"""Shared pytest configuration for the OFT backend test suite.

Two problems this addresses:

1. **No test may hang the whole run.** Several integration tests fetch live market data through
   yfinance, which uses libcurl (``curl_cffi``) under the hood. In throttled / sandboxed networks a
   request can stall for minutes inside ``curl_easy_perform`` (the socket connects but the HTTP
   response never arrives), and libcurl's C loop doesn't honour Python-level timeouts reliably. We
   cap every outbound request (libcurl + plain sockets) so a stalled call fails fast instead of
   wedging the session. This makes the suite *always terminate*.

2. **A clean, deterministic offline run.** Pass ``--offline`` (or set ``OFT_OFFLINE=1``) to skip the
   test modules that require external network (live market data / SEC / FRED / RSS / a hosted LLM).
   Without the flag every test still runs — it just can't hang any more.

Nothing here changes behaviour when the network is healthy and the flag is absent.
"""

from __future__ import annotations

import os
import socket

import pytest

# Hard ceiling (seconds) for any single outbound request. Generous enough for a healthy fetch, low
# enough that a stalled connection fails fast instead of hanging the run.
_REQUEST_TIMEOUT_CAP = 20.0

# Test modules that make real external-network calls (live prices, SEC EDGAR, FRED, RSS, a hosted
# LLM). Skipped only under --offline / OFT_OFFLINE — otherwise they run (now bounded by the caps
# above). Kept as one explicit list so offline runs stay deterministic without per-file markers.
_NETWORK_MODULES: set[str] = {
    # live market/reference data (yfinance / SEC EDGAR / FRED / Alpaca / RSS)
    "test_market",
    "test_metrics",
    "test_rates",
    "test_macro",
    "test_filings",
    "test_listings",
    "test_fundamental_guard",
    "test_index_benchmark",
    "test_news_router",
    "test_equity_stream",
    # backtest / engine / factor pipelines that pull bars for a real universe
    "test_backtest_agent",
    "test_backtest_proposals",
    "test_backtest_timing",
    "test_model_backtest",
    "test_integration_engine",
    "test_engine_modules",
    "test_lab_buy_hold",
    "test_cost_sensitivity",
    "test_strategy_lab_donchian",
    "test_algo_demo",
    "test_factor_monitor",
    "test_factor_monitor_agent",
    # hosted-LLM agents (need a reachable LLM endpoint + key)
    "test_agent_assistant",
    "test_agent_code",
    "test_agent_coder",
    "test_agent_scenarios",
    "test_assistant_agent",
    "test_chart_agent",
    "test_committee_local",
    "test_portfolio_agent",
    "test_research_loop",
}


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--offline",
        action="store_true",
        default=False,
        help="Skip test modules that need external network (live data / SEC / FRED / LLM).",
    )


def _offline(config) -> bool:
    return bool(config.getoption("--offline")) or os.environ.get("OFT_OFFLINE", "") not in ("", "0")


def _is_local_url(url: str) -> bool:
    u = url.lower()
    return any(h in u for h in ("127.0.0.1", "localhost", "://[::1]", "0.0.0.0"))


def _install_request_caps() -> None:
    """Cap libcurl + socket timeouts so no outbound request can hang the run."""
    # plain sockets (httpx / requests / urllib → SEC, FRED, RSS)
    if (socket.getdefaulttimeout() or 1e9) > _REQUEST_TIMEOUT_CAP:
        socket.setdefaulttimeout(_REQUEST_TIMEOUT_CAP)
    # libcurl (yfinance via curl_cffi) — force a total-request timeout it will actually honour
    try:
        from curl_cffi.requests import Session
    except Exception:  # curl_cffi absent → nothing to cap
        return
    if getattr(Session.request, "_oft_capped", False):
        return
    _orig = Session.request

    def _capped(self, method, url, *args, **kwargs):
        given = kwargs.get("timeout")
        try:
            given = float(given) if given is not None else None
        except (TypeError, ValueError):
            given = None
        kwargs["timeout"] = min(given, _REQUEST_TIMEOUT_CAP) if given else _REQUEST_TIMEOUT_CAP
        return _orig(self, method, url, *args, **kwargs)

    _capped._oft_capped = True  # type: ignore[attr-defined]
    Session.request = _capped  # type: ignore[assignment]


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "network: test needs external data/LLM network access")
    _install_request_caps()


def pytest_collection_modifyitems(config, items) -> None:
    if not _offline(config):
        return
    skip = pytest.mark.skip(reason="offline: external network disabled (--offline / OFT_OFFLINE)")
    for item in items:
        module = item.module.__name__.rsplit(".", 1)[-1]
        if module in _NETWORK_MODULES or item.get_closest_marker("network"):
            item.add_marker(skip)
