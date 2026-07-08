"""User settings — the LLM provider (local proxy vs an online OpenAI-compatible API).

The override persists in the data dir and is overlaid onto qhfi's Settings (see
`config.get_engine_settings`), so saving here re-points the assistant, agent workflows,
backtest narration and news sentiment at the chosen provider.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import (
    CRYPTO_DEPTH_SOURCES,
    DEFAULT_RANKING,
    DEPTH_SOURCES,
    EQUITY_BARS_SOURCES,
    EQUITY_REALTIME_SOURCES,
    FICC_CATEGORIES,
    MARKET_DATA_CATEGORIES,
    OPTIONS_CAPS,
    OPTIONS_SOURCES,
    SUPPORTED_EQUITY_FEEDS,
    SUPPORTED_EXCHANGES,
    clear_alpaca_creds,
    clear_llm_api_key,
    get_alpaca_creds,
    get_default_symbol,
    depth_status,
    equity_realtime_enabled,
    get_depth_source,
    get_depth_source_resolved,
    get_engine_settings,
    get_options_caps,
    get_options_default_underlying,
    get_options_enabled,
    get_options_expiry_window,
    get_options_source,
    get_equity_feed,
    get_llm_override,
    get_market_data_config,
    get_mcp_servers,
    get_news_sources,
    get_news_topics,
    get_terminal_settings,
    normalize_llm_base_url,
    set_llm_override,
    set_market_data_config,
    set_mcp_servers,
    set_news_sources,
    set_news_topics,
)
from app.deps import (
    broker_kind,
    get_data_manager,
    get_llm_model,
    reload_llm,
    reload_market_data,
)
from app.services import fundamentals as fa
from app.services import market as mkt
from app.services import mcp_client
from app.services import news_router as nr

router = APIRouter(prefix="/api/settings", tags=["settings"])


class LlmConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class NewsSourceIn(BaseModel):
    name: str = ""
    url: str = ""
    enabled: bool = True
    weight: float = 50  # ranking priority 0–100 (50 = neutral)


class NewsConfigIn(BaseModel):
    builtin: dict[str, bool] = {}
    builtin_weights: dict[str, float] = {}
    custom: list[NewsSourceIn] = []
    max_items: int = 30
    ranking: dict[str, float] = {}  # composite-rank weights + halflife_h (Settings → Ranking)


class NewsTestIn(BaseModel):
    url: str = ""
    name: str = ""


class NewsDiscoverIn(BaseModel):
    query: str = ""


class NewsTopicIn(BaseModel):
    key: str = ""  # ignored on save (re-slugged from first label); echoed back for round-trips
    labels: list[str] = []  # one or more display labels/aliases, all feeding the one query
    label: str = ""  # legacy single-label (still accepted; folded into `labels`)
    query: str = ""  # keyword interest → Google News search feed
    enabled: bool = True


class NewsTopicsIn(BaseModel):
    topics: list[NewsTopicIn] = []


class NewsTopicPreviewIn(BaseModel):
    query: str = ""


class MarketDataIn(BaseModel):
    # Per-asset-class categories — the canonical input (each: source / cache TTL / history / default
    # symbol). The flat fields below are the legacy path, kept so old clients/tests keep working.
    categories: dict | None = None
    exchange: str = ""           # legacy: crypto exchange (→ categories.crypto.source)
    intraday_ttl: float | None = None
    history_years: int | None = None
    equity_feed: str = ""        # legacy: Alpaca equity feed (→ categories.equity.realtime_feed)
    alpaca_api_key: str = ""    # blank keeps the previously saved key
    alpaca_api_secret: str = ""  # blank keeps the previously saved secret
    alpaca_paper: bool = True


class ExchangeTestIn(BaseModel):
    exchange: str = ""


class EquityTestIn(BaseModel):
    # blank key/secret → fall back to the saved Alpaca creds; feed blank → the saved equity feed
    api_key: str = ""
    api_secret: str = ""
    feed: str = ""


class DepthTestIn(BaseModel):
    # blank source → the asset's currently-saved depth source; blank asset → equity
    asset: str = ""
    source: str = ""


class OptionsTestIn(BaseModel):
    # blank source → the saved options source; blank underlying → the saved default underlying
    source: str = ""
    underlying: str = ""


def _state() -> dict:
    """Current LLM-provider view (never leaks the key — only whether one is saved)."""
    ov = get_llm_override()
    eng = get_engine_settings()
    return {
        "custom": bool(ov.get("base_url")),
        "base_url": ov.get("base_url") or eng.llm_base_url,
        "model": ov.get("model") or get_llm_model(),
        "model_pinned": bool(ov.get("model")),
        "has_key": bool(ov.get("api_key")),
    }


def _probe_models(base: str, key: str) -> dict:
    base = normalize_llm_base_url(base)
    headers = {"Authorization": f"Bearer {key}"} if key and key != "not-needed" else {}
    try:
        r = httpx.get(f"{base.rstrip('/')}/models", timeout=8.0, headers=headers)
        r.raise_for_status()
        ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
        return {"ok": True, "detail": f"{base.rstrip('/')} · {len(ids)} model(s)", "models": ids}
    except Exception as e:  # noqa: BLE001 - surface the connection error to the UI
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "models": []}


@router.get("/llm")
def get_llm() -> dict:
    return _state()


@router.get("/llm/models")
def list_models() -> dict:
    """Detect the models the ACTIVE provider serves (local proxy or saved online API)."""
    eng = get_engine_settings()
    out = _probe_models(eng.llm_base_url, eng.llm_api_key)
    out["current"] = get_llm_model()
    return out


@router.put("/llm")
def put_llm(cfg: LlmConfig) -> dict:
    """Save (base_url empty → revert to the local proxy), then hot-reload the LLM clients."""
    set_llm_override(cfg.base_url, cfg.api_key, cfg.model)
    reload_llm()
    return _state()


@router.delete("/llm/key")
def delete_llm_key() -> dict:
    """Remove the saved LLM API key entirely (provider base_url + model are kept), then reload."""
    clear_llm_api_key()
    reload_llm()
    return _state()


@router.post("/llm/test")
def test_llm(cfg: LlmConfig) -> dict:
    """Probe a provider's /models with the given (or currently saved) credentials."""
    eng = get_engine_settings()
    base = cfg.base_url or eng.llm_base_url
    key = cfg.api_key or (get_llm_override().get("api_key") if cfg.base_url else eng.llm_api_key) or eng.llm_api_key
    return _probe_models(base, key)


def _is_local_base(base: str) -> bool:
    """True if the base URL points at a local host (loopback / .local)."""
    host = (urlparse(normalize_llm_base_url(base)).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local")


def _probe_chat(base: str, key: str, model: str, local: bool) -> dict:
    """Issue a tiny real chat completion and report latency.

    Local providers (the auto-swap proxy) may cold-load the model on the first call, so they get
    a long read timeout and slowness is expected; online APIs should answer fast, so they get a
    short timeout and a slow reply is flagged.
    """
    base = normalize_llm_base_url(base)
    headers = {"Authorization": f"Bearer {key}"} if key and key != "not-needed" else {}
    timeout = httpx.Timeout(connect=5.0, read=120.0 if local else 20.0, write=10.0, pool=5.0)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly one word: pong"}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": False,
    }
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{base.rstrip('/')}/chat/completions", json=payload, timeout=timeout, headers=headers)
        r.raise_for_status()
        elapsed = time.perf_counter() - t0
        reply = ((r.json().get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
        if local:
            note = " (local — first call may warm the model)"
        elif elapsed > 8.0:
            note = " (slow for an online API)"
        else:
            note = ""
        detail = (
            f"{model} · replied in {elapsed:.1f}s{note}"
            if reply
            else f"{model} · responded in {elapsed:.1f}s but returned no text"
        )
        return {"ok": True, "detail": detail, "latency_ms": round(elapsed * 1000), "model": model, "reply": reply[:200], "local": local}
    except Exception as e:  # noqa: BLE001 - surface the connection/timeout error to the UI
        elapsed = time.perf_counter() - t0
        kind = type(e).__name__
        hint = ""
        if "Timeout" in kind:
            hint = " — model may still be loading, retry" if local else " — provider did not respond in time"
        detail = f"{kind}: {str(e)[:160]}{hint}"
        return {"ok": False, "detail": detail, "latency_ms": round(elapsed * 1000), "model": model, "reply": "", "local": local}


@router.post("/llm/probe")
def probe_llm(cfg: LlmConfig) -> dict:
    """Live generation probe: send a real one-word completion to confirm the model actually replies.

    Unlike /llm/test (which only lists the served /models), this exercises the full request path and
    measures round-trip latency. The timeout adapts to the provider: a local proxy can cold-load the
    model on the first call (long timeout, slowness expected), an online API should respond at once.
    """
    eng = get_engine_settings()
    base = cfg.base_url or eng.llm_base_url
    key = cfg.api_key or (get_llm_override().get("api_key") if cfg.base_url else eng.llm_api_key) or eng.llm_api_key
    local = _is_local_base(base) if (cfg.base_url or "").strip() else True
    model = (cfg.model or "").strip()
    if not model:  # resolve the model the active/target provider would actually use
        if local:
            model = get_llm_model()
        else:
            probe = _probe_models(base, key)
            model = probe["models"][0] if probe["models"] else ""
    if not model:
        return {"ok": False, "detail": "no model resolved — pick or type a model id", "latency_ms": 0, "model": "", "reply": "", "local": local}
    return _probe_chat(base, key, model, local)


# ── News sources ─────────────────────────────────────────────────────────────────
def _news_state() -> dict:
    cfg = get_news_sources()
    return {
        "builtin": cfg["builtin"],
        "builtin_weights": cfg["builtin_weights"],
        "builtin_meta": nr.builtin_source_meta(),
        "custom": cfg["custom"],
        "max_items": cfg["max_items"],
        "ranking": cfg["ranking"],
        "ranking_default": dict(DEFAULT_RANKING),
    }


@router.get("/news")
def get_news() -> dict:
    return _news_state()


@router.put("/news")
def put_news(cfg: NewsConfigIn) -> dict:
    """Save which feeds run (+ their ranking weights), then clear the news cache so it shows."""
    set_news_sources(
        cfg.builtin,
        cfg.builtin_weights,
        [c.model_dump() for c in cfg.custom],
        cfg.max_items,
        cfg.ranking,
    )
    nr.clear_news_cache()
    return _news_state()


@router.post("/news/test")
def test_news(body: NewsTestIn) -> dict:
    """Fetch a custom RSS feed for a sample symbol and report how many items it returned."""
    url = (body.url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "detail": "URL must start with http:// or https://", "sample": []}
    try:
        items = fa._fetch_rss(url.replace("{symbol}", "AAPL"), body.name or "test")
    except Exception as e:  # noqa: BLE001 - surface the fetch error to the UI
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "sample": []}
    sample = [it["title"] for it in items[:5]]
    return {"ok": bool(items), "detail": f"{len(items)} item(s)" if items else "no items returned", "sample": sample}


@router.post("/news/discover")
def discover_news(body: NewsDiscoverIn) -> dict:
    """Live-discover RSS feeds for a keyword or site so users can add sources without a URL."""
    q = (body.query or "").strip()
    if not q:
        return {"ok": False, "detail": "Enter a site or keyword", "candidates": []}
    cands = nr.discover_feeds(q)
    return {
        "ok": bool(cands),
        "detail": f"{len(cands)} feed(s) found" if cands else "no feeds found",
        "candidates": cands,
    }


# ── News topics ("interest subscriptions") ─────────────────────────────────────────
@router.get("/news/topics")
def get_news_topics_cfg() -> dict:
    """User topics for the Settings editor (built-in Market/Macro are managed separately)."""
    return {"topics": get_news_topics()}


@router.put("/news/topics")
def put_news_topics(body: NewsTopicsIn) -> dict:
    """Save user topics (keys re-slugged uniquely), then clear the news cache so they show."""
    saved = set_news_topics([t.model_dump() for t in body.topics])
    nr.clear_news_cache()
    return {"topics": saved}


@router.post("/news/topics/preview")
def preview_news_topic(body: NewsTopicPreviewIn) -> dict:
    """Fetch a keyword interest's Google News feed and report a sample, before saving the topic."""
    q = (body.query or "").strip()
    if not q:
        return {"ok": False, "detail": "Enter an interest / keyword", "sample": []}
    try:
        items = fa._fetch_rss(nr._topic_query_url(q), "preview")
    except Exception as e:  # noqa: BLE001 - surface the fetch error to the UI
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "sample": []}
    sample = [it["title"] for it in items[:5]]
    return {"ok": bool(items), "detail": f"{len(items)} item(s)" if items else "no items returned", "sample": sample}


# ── Market data ────────────────────────────────────────────────────────────────────
def _market_data_state() -> dict:
    """Market-data config + read-only status for the Settings editor (never leaks the secret)."""
    cfg = get_market_data_config()
    term = get_terminal_settings()
    # cached daily-bars symbols = parquet files under <data_dir>/market/<asset_class>/
    market_root = term.data_dir / "market"
    cached_symbols = sum(1 for _ in market_root.rglob("*.parquet")) if market_root.exists() else 0
    from app.services.realtime import get_hub

    # Equity real-time streaming needs an (auto-)resolved 'alpaca' source AND configured creds.
    equity_rt_on = equity_realtime_enabled()
    return {
        "categories": cfg["categories"],
        # Selectable options per category, so the Settings UI can render the dropdowns.
        "category_meta": {
            "categories": list(MARKET_DATA_CATEGORIES),
            "equity": {
                "bars_sources": list(EQUITY_BARS_SOURCES),
                "realtime_sources": list(EQUITY_REALTIME_SOURCES),
                "feeds": list(SUPPORTED_EQUITY_FEEDS),
                "depth_sources": list(DEPTH_SOURCES),
            },
            "crypto": {
                "sources": list(SUPPORTED_EXCHANGES),
                "depth_sources": list(CRYPTO_DEPTH_SOURCES),
            },
            # FICC classes now expose a selectable order-book depth source (like equity).
            **{cat: {"depth_sources": list(DEPTH_SOURCES)} for cat in FICC_CATEGORIES},
            # Options chain source (standalone; not an OHLCV category) + per-source capabilities.
            "options": {"sources": list(OPTIONS_SOURCES), "capabilities": OPTIONS_CAPS},
        },
        # Per-class order-book status: the chosen source, the hub topic token the frontend uses to
        # subscribe (empty when off), and whether depth is available right now.
        # depth_status resolves 'auto' once per asset (consistent source/token pair; 1 probe not 3).
        # This route is sync `def`, so FastAPI already runs it on a worker thread — cold probes here
        # never touch the event loop.
        "depth": {a: depth_status(a) for a in MARKET_DATA_CATEGORIES},
        # Options-chain status: active source, capabilities, and seed knobs (for the widget/UI).
        "options": {
            "source": get_options_source(),
            "enabled": get_options_enabled(),
            "capabilities": get_options_caps(),
            "default_underlying": get_options_default_underlying(),
            "expiry_window": get_options_expiry_window(),
        },
        # legacy top-level mirror (back-compat for older clients + the status block)
        "exchange": cfg["exchange"],
        "supported": list(SUPPORTED_EXCHANGES),
        "intraday_ttl": cfg["intraday_ttl"],
        "history_years": cfg["history_years"],
        "equity_feed": cfg["equity_feed"],
        "supported_equity_feeds": list(SUPPORTED_EQUITY_FEEDS),
        "equity_stream": {"enabled": equity_rt_on, "feed": cfg["equity_feed"]},
        "has_alpaca_key": bool(cfg["alpaca_api_key"]),
        "alpaca_paper": cfg["alpaca_paper"],
        "broker": broker_kind(),
        "data_dir": str(term.data_dir),
        "lake_dir": str(term.qhfi_lake_dir),
        "cached_symbols": cached_symbols,
        "realtime": get_hub().stats(),
    }


@router.get("/market-data")
def get_market_data() -> dict:
    return _market_data_state()


@router.put("/market-data")
def put_market_data(cfg: MarketDataIn) -> dict:
    """Persist the market-data config, then hot-reload the data manager + broker."""
    set_market_data_config(
        cfg.exchange,
        cfg.intraday_ttl,
        cfg.history_years,
        cfg.alpaca_api_key,
        cfg.alpaca_api_secret,
        cfg.alpaca_paper,
        cfg.equity_feed,
        categories=cfg.categories,
    )
    reload_market_data()
    return _market_data_state()


@router.delete("/market-data/alpaca")
def delete_alpaca_creds() -> dict:
    """Remove the saved Alpaca credentials entirely; broker falls back to the local sim."""
    clear_alpaca_creds()
    reload_market_data()
    return _market_data_state()


@router.post("/market-data/test")
def test_market_data(body: ExchangeTestIn) -> dict:
    """Probe a ccxt exchange's markets so the user can confirm connectivity before saving."""
    ex_id = (body.exchange or "").strip().lower()
    if ex_id not in SUPPORTED_EXCHANGES:
        return {"ok": False, "detail": f"unsupported exchange '{ex_id}' (use {list(SUPPORTED_EXCHANGES)})"}
    try:
        import ccxt

        klass = getattr(ccxt, ex_id, None)
        if klass is None:
            return {"ok": False, "detail": f"ccxt has no exchange '{ex_id}'"}
        markets = klass({"enableRateLimit": True}).load_markets()
        return {"ok": True, "detail": f"{ex_id} · {len(markets)} markets"}
    except Exception as e:  # noqa: BLE001 - surface the connection error to the UI
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


@router.post("/market-data/test-equity")
def test_equity_source(body: EquityTestIn) -> dict:
    """Probe Alpaca's market-data API with the given (or saved) credentials + feed.

    Confirms the equity realtime source works before saving: fetches AAPL's latest trade on the
    chosen feed (IEX free / SIP paid). A blank key/secret falls back to the saved Alpaca creds.
    """
    key = (body.api_key or "").strip()
    secret = (body.api_secret or "").strip()
    if not key or not secret:
        saved_key, saved_secret, _ = get_alpaca_creds()
        key = key or saved_key
        secret = secret or saved_secret
    if not key or not secret:
        return {"ok": False, "detail": "no Alpaca API key/secret configured"}
    feed = (body.feed or "").strip().lower()
    if feed not in SUPPORTED_EQUITY_FEEDS:
        feed = get_equity_feed()
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        client = StockHistoricalDataClient(key, secret)
        req = StockLatestTradeRequest(symbol_or_symbols="AAPL", feed=DataFeed[feed.upper()])
        trade = client.get_stock_latest_trade(req).get("AAPL")
        price = getattr(trade, "price", None)
        if price is None:
            return {"ok": True, "detail": f"Alpaca {feed.upper()} · credentials valid (no AAPL trade)"}
        return {"ok": True, "detail": f"Alpaca {feed.upper()} · AAPL ${price}"}
    except Exception as e:  # noqa: BLE001 - surface the auth/connection error to the UI
        msg = str(e)
        if "401" in msg or "unauthorized" in msg.lower() or "forbidden" in msg.lower():
            return {"ok": False, "detail": "invalid Alpaca API key/secret (401 Unauthorized)"}
        if feed == "sip" and ("403" in msg or "subscription" in msg.lower()):
            return {"ok": False, "detail": "key lacks a SIP data subscription — try the IEX feed"}
        return {"ok": False, "detail": f"{type(e).__name__}: {msg[:160]}"}


@router.post("/market-data/test-depth")
def test_depth_source(body: DepthTestIn) -> dict:
    """Probe an order-book depth source for an asset class before saving.

    Confirms a mid is obtainable and (for the simulated source) that a book can be built. A blank
    source falls back to the asset's saved depth source. Real vendor sources report whether their
    provider is installed + configured; the simulated source always works when a mid is available.
    """
    asset = (body.asset or "equity").strip().lower()
    if asset not in MARKET_DATA_CATEGORIES:
        return {"ok": False, "detail": f"unknown asset class '{asset}'"}
    source = (body.source or get_depth_source(asset)).strip().lower()
    auto_note = ""
    if source == "auto":  # test what auto resolves to right now, and say so in the result
        source = get_depth_source_resolved(asset)
        auto_note = "auto → "
    if source in ("", "none"):
        return {"ok": False, "detail": "depth is off for this asset class"}
    if asset == "crypto" and source == "exchange":
        return {"ok": True, "detail": f"{auto_note}crypto uses the live {get_market_data_config()['exchange']} L2 book"}
    try:
        from app.services.depth import build_depth_source, latest_mid, synthetic_book_frame

        mgr = build_depth_source(source)
        if mgr is None:
            return {"ok": False, "detail": f"{auto_note}depth source '{source}' is not installed"}
        if not mgr.enabled(asset):
            return {"ok": False, "detail": f"{auto_note}{source}: not configured for {asset}"}
        sym = get_default_symbol(asset)
        mid = latest_mid(asset, sym)
        if mid is None:
            return {"ok": False, "detail": f"{auto_note}{source}: no mid for {sym} (market closed / unknown symbol?)"}
        levels = len(synthetic_book_frame(mid)["bids"]) if source == "sim" else 0
        detail = f"{auto_note}{source} · {asset} {sym} mid {mid:.4f}"
        return {"ok": True, "detail": f"{detail} · {levels} levels/side" if levels else detail}
    except Exception as e:  # noqa: BLE001 - surface the probe error to the UI
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


@router.post("/market-data/test-options")
def test_options_source(body: OptionsTestIn) -> dict:
    """Probe the options-chain source: confirm it's configured and returns a chain for a symbol."""
    source = (body.source or get_options_source()).strip().lower()
    if source in ("", "none"):
        return {"ok": False, "detail": "options source is off"}
    underlying = (body.underlying or get_options_default_underlying()).strip().upper()
    try:
        from app.services.options import build_options_source

        src = build_options_source(source)
        if src is None:
            return {"ok": False, "detail": f"options source '{source}' is not installed"}
        if not src.enabled():
            return {"ok": False, "detail": f"{source}: not configured"}
        exps = src.expirations(underlying)
        if not exps:
            return {"ok": False, "detail": f"{source}: no chain for {underlying} (market closed / unknown symbol?)"}
        rows = src.chain(underlying, exps[0])
        return {"ok": True,
                "detail": f"{source} · {underlying} · {len(exps)} expiries · {len(rows)} contracts @ {exps[0]}"}
    except Exception as e:  # noqa: BLE001 - surface the probe error to the UI
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


@router.post("/market-data/clear-cache")
def clear_market_data_cache() -> dict:
    """Drop the transient intraday cache + rebuild the data manager (forces fresh fetches)."""
    dropped = mkt.clear_intraday_cache()
    get_data_manager.cache_clear()
    return {"ok": True, "detail": f"cleared {dropped} intraday cache entr{'y' if dropped == 1 else 'ies'}"}


# ── External MCP servers (assistant tool providers) ──────────────────────────────────
class McpServerIn(BaseModel):
    name: str = ""
    transport: str = "stdio"          # "stdio" | "http"
    command: str = ""                 # stdio: executable to spawn
    args: list[str] = []              # stdio: command arguments
    env: dict[str, str] = {}          # stdio: extra environment (merged onto the real env)
    url: str = ""                     # http: streamable-HTTP endpoint
    headers: dict[str, str] = {}      # http: request headers
    enabled: bool = True


class McpServersIn(BaseModel):
    servers: list[McpServerIn] = []


@router.get("/mcp")
def get_mcp() -> dict:
    """The configured external MCP servers (whose tools the grounded assistant can call)."""
    return {"servers": get_mcp_servers()}


@router.put("/mcp")
def put_mcp(body: McpServersIn) -> dict:
    """Validate + persist the MCP server list, then drop the assistant's tool-discovery cache."""
    saved = set_mcp_servers([s.model_dump() for s in body.servers])
    mcp_client.clear_cache()
    return {"servers": saved}


@router.post("/mcp/test")
async def test_mcp(body: McpServerIn) -> dict:
    """Connect to one MCP server and list its tools, so users can verify it before saving."""
    server = body.model_dump()
    tools = await mcp_client.probe_server(server)
    return {
        "ok": bool(tools),
        "detail": f"{len(tools)} tool(s)" if tools else "no tools (unreachable or empty)",
        "tools": [{"name": t["tool"], "description": t["description"]} for t in tools],
    }


# ── Market-data vendor providers (depth/options credentials) ─────────────────────────
# Databento / Polygon / Tradier / dxFeed / IBKR keys were previously env-var only; this lets the UI
# enter them (stored encrypted via config's provider store). After a save the depth/options managers
# are reloaded so the new creds take effect live. The GET view never leaks a secret.
class ProviderIn(BaseModel):
    name: str = ""            # databento | polygon | tradier | dxfeed | ibkr
    api_key: str = ""         # databento / polygon (blank keeps saved)
    token: str = ""           # tradier (blank keeps saved)
    address: str = ""         # dxfeed (blank keeps saved)
    env: str = ""             # tradier: "live" | "sandbox"
    host: str = ""            # ibkr
    port: str = ""            # ibkr


@router.get("/providers")
def get_providers() -> dict:
    """Saved vendor-provider status (has_key / from_env / non-secret fields) — never leaks a secret."""
    from app.config import provider_state

    return {"providers": provider_state()}


@router.put("/providers")
def put_provider(body: ProviderIn) -> dict:
    """Persist one provider's creds/settings, then hot-reload the depth/options managers."""
    from app.config import provider_state, set_provider_config

    fields = {k: v for k, v in body.model_dump().items() if k != "name"}
    try:
        set_provider_config(body.name, fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    reload_market_data()
    return {"providers": provider_state()}


@router.delete("/providers/{name}")
def delete_provider(name: str) -> dict:
    """Remove a provider's saved creds entirely, then hot-reload the depth/options managers."""
    from app.config import clear_provider_secret, provider_state

    clear_provider_secret(name)
    reload_market_data()
    return {"providers": provider_state()}


# ── Filesystem browse (in-app directory picker for the Settings dir fields) ───────────
@router.get("/fs/list")
def fs_list(path: str = "") -> dict:
    """List the sub-directories of a path, for the in-app folder picker.

    Directories only — never file contents. An empty/unknown/denied path falls back to the home dir
    (with an ``error`` note). This is a localhost-only personal app; the backend already reads/writes
    the filesystem freely, so listing directory names here is acceptable.
    """
    import os
    from pathlib import Path

    term = get_terminal_settings()
    # quick-jump roots the UI offers (dedup, keep order)
    roots: list[str] = []
    for r in (str(Path.home()), os.getcwd(), str(term.data_dir)):
        if r not in roots:
            roots.append(r)

    raw = (path or "").strip()
    error = ""
    try:
        target = (Path(raw).expanduser() if raw else Path.home()).resolve()
    except (OSError, RuntimeError, ValueError):
        target = Path.home()
    if not target.exists() or not target.is_dir():
        if raw:
            error = f"not a directory: {target}"
        target = Path.home()

    entries: list[dict] = []
    try:
        for e in sorted(os.scandir(target), key=lambda x: x.name.lower()):
            if e.name.startswith("."):
                continue  # hide dotfiles/dirs
            try:
                if e.is_dir():
                    entries.append({"name": e.name, "is_dir": True})
            except OSError:
                continue
    except (PermissionError, OSError) as ex:
        error = f"{type(ex).__name__}: {ex}"

    parent = str(target.parent) if target.parent != target else ""
    return {
        "path": str(target),
        "parent": parent,
        "sep": os.sep,
        "entries": entries,
        "roots": roots,
        "error": error,
    }
