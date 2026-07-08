"""Health / readiness — reports qhfi import status and vLLM proxy reachability."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter

from app.config import (
    MARKET_DATA_CATEGORIES,
    depth_status,
    equity_realtime_enabled,
    get_crypto_exchange,
    get_crypto_realtime_enabled,
    get_engine_settings,
    get_options_caps,
    get_options_default_underlying,
    get_options_enabled,
    get_options_expiry_window,
    get_options_source,
    get_equity_feed,
    get_trades_enabled,
    get_trades_source,
    get_trades_topic_token,
    get_terminal_settings,
    normalize_llm_base_url,
)
from app.deps import _auth_headers, get_llm_model
from app.services.bootstrap import status as bootstrap_status
from app.services.universe import list_universes

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict:
    eng = get_engine_settings()
    term = get_terminal_settings()

    # qhfi is import-checked simply by reaching here (these imports resolve at module load).
    qhfi_ok = True

    # LLM endpoint: probe the OpenAI-compatible /models with a short timeout. Online providers
    # (DeepSeek) 401 an unauthenticated /models, so send the Bearer key when one is saved; their
    # cold TLS handshake can graze 2s, so allow 5 (a down local proxy still fails fast — refused).
    llm_ok, llm_detail = False, "unreachable"
    base = normalize_llm_base_url(eng.llm_base_url)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/models", headers=_auth_headers(eng.llm_api_key))
            llm_ok = r.status_code == 200
            llm_detail = "ok" if llm_ok else f"http {r.status_code}"
    except Exception as e:  # noqa: BLE001 - probe failures are expected when the stack is down
        llm_detail = type(e).__name__

    return {
        "status": "ok",
        "qhfi": {"ok": qhfi_ok, "llm_model": get_llm_model()},
        "llm": {"ok": llm_ok, "detail": llm_detail, "base_url": eng.llm_base_url},
        "data_dir": str(term.data_dir),
        "crypto_exchange": get_crypto_exchange(),
        # Equity realtime needs an (auto-)resolved Alpaca source AND credentials present; the
        # per-category "Realtime: Off" toggle (realtime_source=none) disables it without removing
        # the keys, and 'auto' turns it on exactly when creds exist.
        "equity_stream": {
            "enabled": equity_realtime_enabled(),
            "feed": get_equity_feed(),
        },
        # Crypto realtime: on/off per the category toggle (bars/charts keep working when off).
        "crypto_stream": {"enabled": get_crypto_realtime_enabled()},
        # Per-class order-book depth: the ACTIVE source ('auto' already resolved — what the widget
        # tags), the configured value (so the UI can show "auto → sim"), the hub topic token to
        # subscribe on (empty when off), and whether depth is available now. Computed in a worker
        # thread: a cold 'auto' probe does real network I/O (IB handshake, Databento live gateway)
        # and must never stall the event loop that serves every websocket.
        "depth": await asyncio.to_thread(
            lambda: {a: depth_status(a) for a in MARKET_DATA_CATEGORIES}
        ),
        # Per-class time-&-sales (tape): the active source, the hub topic token to subscribe on (empty
        # when off), and whether a tape is available now. Lets the Time & Sales widget switch
        # live/empty per asset class — crypto/equity real feeds + the simulated FICC tape — without
        # loading Settings. Mirrors the depth block's token scheme.
        "trades": {
            a: {"source": get_trades_source(a), "token": get_trades_topic_token(a),
                "enabled": get_trades_enabled(a)}
            for a in MARKET_DATA_CATEGORIES
        },
        # Options-chain status: active source, capabilities, seed underlying/window (drives the
        # Options Chain widget's live/unavailable state + greeks-column visibility).
        "options": {
            "source": get_options_source(),
            "enabled": get_options_enabled(),
            "capabilities": get_options_caps(),
            "default_underlying": get_options_default_underlying(),
            "expiry_window": get_options_expiry_window(),
        },
        "universes": list_universes(),
        # First-run data bootstrap progress (idle/running/done/skipped/error).
        "bootstrap": bootstrap_status(),
    }
