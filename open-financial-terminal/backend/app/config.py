"""Terminal configuration.

Two layers of settings:

* ``TerminalSettings`` — terminal-owned paths and CORS (OFT_ prefix).
* qhfi ``Settings`` — the engine's config (LLM endpoint/model, QHFI_ prefix), reused as-is
  so the local vLLM stack has a single source of truth.

Paths are resolved relative to the backend directory so the app runs the same regardless of
the process working directory.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from pydantic_settings import BaseSettings, SettingsConfigDict

from qhfi.core.config import Settings as QhfiSettings
from qhfi.core.config import get_settings as get_qhfi_settings

from app.secret_store import decrypt_secret, encrypt_secret

BACKEND_DIR = Path(__file__).resolve().parent.parent          # .../Open Financial Terminal/backend
PROJECT_DIR = BACKEND_DIR.parent                              # .../Open Financial Terminal
_DEFAULT_UNIVERSES = PROJECT_DIR.parent / "quant-hedge-fund-incubator" / "config" / "instruments"


class TerminalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OFT_", env_file=BACKEND_DIR / ".env", extra="ignore")

    data_dir: Path = BACKEND_DIR / "data"
    universe_dir: Path = _DEFAULT_UNIVERSES
    # The qhfi parquet lake (13F holdings, CUSIP crosswalk, original filings + insider cache).
    # Defaults to the sibling qhfi project's lake so the Public Filings module reads the data
    # its pull scripts produce; cache-to-lake writes land here too.
    qhfi_lake_dir: Path = PROJECT_DIR.parent / "quant-hedge-fund-incubator" / "data" / "lake"
    db_path: Path = BACKEND_DIR / "oft.sqlite"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Default ccxt exchange for crypto data and realtime streams. Kraken is reachable from
    # US/geo-restricted IPs where binance.com returns HTTP 451.
    crypto_exchange: str = "kraken"

    # CrewAI service (Investment Committee module): the standalone crewai-service that hosts the
    # committee crew + knowledge bases. The terminal proxies/relays to it.
    committee_base_url: str = "http://localhost:8083"

    # Optional explicit LLM model id. If unset, the terminal resolves a served model from the
    # proxy's /v1/models (preferring a gemma) so it works without knowing the exact tag.
    llm_model: str | None = None
    llm_model_prefer: str = "gemma"

    # Paper trading. With no Alpaca key the terminal uses a local simulator seeded with this
    # cash; set OFT_ALPACA_API_KEY/SECRET to route orders to Alpaca's hosted paper environment.
    paper_initial_cash: float = 100_000.0
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_paper: bool = True

    # Automatic background data-refresh (DataRefreshRunner). A master enable + per-job default
    # cadences (seconds) seed the registry; the SQLite config store holds live per-job overrides
    # set from Settings → Data Refresh, so changes persist without a restart. Equity bars are
    # skipped off US-market-hours when data_refresh_market_hours_only is on (daily yfinance bars
    # don't change overnight/weekends); crypto + global jobs ignore the gate.
    data_refresh_enabled: bool = True
    data_refresh_bars_s: int = 900        # daily-bar lake (active watchlist/holdings/algo names)
    data_refresh_news_s: int = 600        # warm the live news cache for active equity symbols
    data_refresh_rates_s: int = 86400     # Treasury curve (FRED)
    data_refresh_macro_s: int = 86400     # US macro indicators (FRED)
    data_refresh_filings_s: int = 86400   # SEC filings feed (active equities; needs SEC_USER_AGENT)
    data_refresh_market_hours_only: bool = True

    def resolve(self) -> "TerminalSettings":
        """Make all paths absolute (relative paths are taken against backend/)."""
        def _abs(p: Path) -> Path:
            return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

        self.data_dir = _abs(self.data_dir)
        self.universe_dir = _abs(self.universe_dir)
        self.qhfi_lake_dir = _abs(self.qhfi_lake_dir)
        self.db_path = _abs(self.db_path)
        return self

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_terminal_settings() -> TerminalSettings:
    s = TerminalSettings().resolve()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


# ── LLM provider override ────────────────────────────────────────────────────────
# Users can point the terminal at an online LLM API (OpenAI-compatible) from Settings.
# The choice persists as a small JSON file in the data dir and is overlaid onto qhfi's
# Settings, so every consumer (assistant, agent, backtest, fundamentals) picks it up.

def _llm_override_path() -> Path:
    return get_terminal_settings().data_dir / "llm_provider.json"


def get_llm_override() -> dict:
    """Saved provider override {base_url, api_key, model}. Empty dict = use the local proxy."""
    p = _llm_override_path()
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                # The api_key is stored encrypted (enc:…); decrypt for in-process use. Legacy
                # plaintext values pass through unchanged and get re-encrypted on the next save.
                if data.get("api_key"):
                    data["api_key"] = decrypt_secret(str(data["api_key"]))
                return data
            return {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def normalize_llm_base_url(base_url: str) -> str:
    """Return the OpenAI-compatible API base URL used for /models and /chat/completions.

    Open WebUI serves its OpenAI-compatible API at /openai/v1, while users often paste the
    UI root (for example http://localhost:3000). Keep explicit /v1 URLs untouched.
    """
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        return urlunparse(parsed._replace(path=path))
    if not path or path == "/":
        host = (parsed.netloc or "").lower()
        if host.endswith(":3000") or "openwebui" in host or "open-webui" in host:
            return urlunparse(parsed._replace(path="/openai/v1"))
    return base_url


def set_llm_override(base_url: str, api_key: str, model: str) -> dict:
    """Persist (or clear) the provider override.

    Three shapes: full default (no base_url, no model → file removed, local proxy + auto model);
    local proxy with a PINNED model ({model} only — lets the user switch between the proxy's
    local LLMs); an online provider ({base_url, api_key, model}). An empty api_key with an
    unchanged base_url keeps the previously saved key (so the UI can show it masked).
    """
    p = _llm_override_path()
    base_url = normalize_llm_base_url(base_url)
    api_key = (api_key or "").strip()
    model = (model or "").strip()
    if not base_url and not model:
        p.unlink(missing_ok=True)
        return {}
    if base_url:
        if not api_key:
            prev = get_llm_override()
            if prev.get("base_url") == base_url:
                api_key = prev.get("api_key", "")
        cfg = {"base_url": base_url, "api_key": api_key, "model": model}
    else:
        cfg = {"model": model}  # local proxy, pinned to a specific served model
    # Persist the api_key encrypted (never plaintext on disk); the returned view is plaintext so
    # callers/tests see the resolved config — same trick as set_market_data_config().
    to_store = {**cfg, "api_key": encrypt_secret(cfg["api_key"])} if cfg.get("api_key") else cfg
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(to_store), "utf-8")
    return cfg


def clear_llm_api_key() -> dict:
    """Remove only the saved LLM API key, keeping the provider base_url + pinned model.

    Unlike set_llm_override's blank-keeps-saved behavior (which traps the key with no removal path),
    this drops the key entirely from disk. The override then supplies no key, so get_engine_settings
    falls back to "not-needed" (fine for keyless/local-auth providers — e.g. a Tailscale-exposed
    proxy). If no base_url and no pinned model remain, the override file is removed (revert to the
    local proxy + auto model). Returns the resolved override; callers should follow with
    deps.reload_llm() to drop the cached clients.
    """
    prev = get_llm_override()
    base_url = prev.get("base_url", "")
    model = prev.get("model", "")
    p = _llm_override_path()
    if not base_url and not model:
        p.unlink(missing_ok=True)
        return {}
    cfg = {"base_url": base_url, "model": model} if base_url else {"model": model}  # no api_key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg), "utf-8")
    return cfg


# ── External MCP servers (the assistant consumes these) ────────────────────────────
# Users register external Model Context Protocol servers whose tools the grounded assistant can
# call mid-answer (alongside its native quote/screen/etc. tools). Persists as a small JSON file in
# the data dir, same pattern as the LLM/news overrides. Each entry:
#   {name, transport: "stdio"|"http", command, args[], env{}, url, headers{}, enabled}
# stdio → spawn `command args...`; http → connect to `url` (streamable-HTTP). Everything is local/
# trusted, so values are stored as-is (no encryption); revisit if remote tokens are ever added.

_MAX_MCP_SERVERS = 20


def _mcp_servers_path() -> Path:
    return get_terminal_settings().data_dir / "mcp_servers.json"


def _clean_mcp_server(raw: object) -> dict | None:
    """Validate one server entry → normalized dict, or None if unusable.

    Requires a name and, by transport, a command (stdio) or an http(s) url (http). A bad/missing
    transport is inferred from whichever of command/url is present.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    command = str(raw.get("command") or "").strip()
    url = str(raw.get("url") or "").strip()
    transport = str(raw.get("transport") or "").strip().lower()
    if transport not in ("stdio", "http"):
        transport = "stdio" if command else "http"
    args = [str(a) for a in raw.get("args") or [] if str(a).strip()]
    env = {str(k): str(v) for k, v in (raw.get("env") or {}).items()} if isinstance(raw.get("env"), dict) else {}
    headers = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()} if isinstance(raw.get("headers"), dict) else {}
    if transport == "stdio" and not command:
        return None
    if transport == "http" and not url.lower().startswith(("http://", "https://")):
        return None
    return {
        "name": name,
        "transport": transport,
        "command": command,
        "args": args,
        "env": env,
        "url": url,
        "headers": headers,
        "enabled": bool(raw.get("enabled", True)),
    }


def get_mcp_servers() -> list[dict]:
    """Saved external MCP server entries (empty list if none/unreadable)."""
    p = _mcp_servers_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = data.get("servers") if isinstance(data, dict) else data
    out: list[dict] = []
    if isinstance(items, list):
        for s in items:
            cleaned = _clean_mcp_server(s)
            if cleaned:
                out.append(cleaned)
    return out


def set_mcp_servers(servers: list) -> list[dict]:
    """Validate + persist external MCP server entries (names de-duped, list capped)."""
    out: list[dict] = []
    used: set[str] = set()
    for s in (servers or [])[:_MAX_MCP_SERVERS]:
        cleaned = _clean_mcp_server(s)
        if not cleaned:
            continue
        base = cleaned["name"]
        name, n = base, 2
        while name in used:
            name = f"{base}-{n}"
            n += 1
        used.add(name)
        cleaned["name"] = name
        out.append(cleaned)
    p = _mcp_servers_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"servers": out}), "utf-8")
    return out


@lru_cache
def get_engine_settings() -> QhfiSettings:
    """qhfi Settings (LLM endpoint/model), with the terminal's saved provider override applied."""
    s = get_qhfi_settings().model_copy(deep=True)
    ov = get_llm_override()
    if ov.get("base_url"):
        s.llm_base_url = normalize_llm_base_url(ov["base_url"])
        s.llm_api_key = ov.get("api_key") or "not-needed"
    if ov.get("model"):  # applies to both online and a pinned local model
        s.llm_model = ov["model"]
    return s


# ── News sources override ─────────────────────────────────────────────────────────
# Users manage which news feeds the News widget pulls from (Settings → News Sources).
# Persists as a small JSON file in the data dir; `services.fundamentals` reads it to build
# the active source list (enabled built-ins + custom RSS feeds).

_BUILTIN_NEWS_KEYS = ("yfinance", "yahoo_rss", "gnews_rss")
# Per-source priority for the ranking router (0–100); 50 = neutral. Higher = up-ranked.
DEFAULT_NEWS_WEIGHT = 50.0

# Ranking-formula parameters (Settings → News Sources → Ranking). The five weights set how much
# each signal contributes to a headline's composite score; halflife_h controls how fast the
# recency term decays. Weights are blended then normalised by their sum, so only their *ratios*
# matter — doubling them all changes nothing. Defaults sum to 1.0.
_RANKING_WEIGHT_KEYS = ("recency", "source", "relevance", "sentiment", "match")
DEFAULT_RANKING: dict = {
    "recency": 0.35,
    "source": 0.20,
    "relevance": 0.25,
    "sentiment": 0.10,
    "match": 0.10,
    "halflife_h": 18.0,
}

DEFAULT_NEWS: dict = {
    "builtin": {k: True for k in _BUILTIN_NEWS_KEYS},
    "builtin_weights": {k: DEFAULT_NEWS_WEIGHT for k in _BUILTIN_NEWS_KEYS},
    "custom": [],
    "ranking": dict(DEFAULT_RANKING),
}


def _clamp_weight(value: object) -> float:
    """Coerce a weight to a float in [0, 100], falling back to the neutral default."""
    try:
        return max(0.0, min(100.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_NEWS_WEIGHT


def _clamp_unit(value: object, default: float) -> float:
    """Coerce a ranking weight to a float in [0, 1], falling back to its default."""
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clamp_halflife(value: object, default: float = 18.0) -> float:
    """Recency half-life in hours, kept sane (1h … 7d)."""
    try:
        return max(1.0, min(168.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _merge_ranking(data: object) -> dict:
    """Saved ranking params over defaults — each weight clamped to [0,1], half-life to [1,168]h."""
    out = dict(DEFAULT_RANKING)
    if isinstance(data, dict):
        for k in _RANKING_WEIGHT_KEYS:
            if k in data:
                out[k] = _clamp_unit(data[k], DEFAULT_RANKING[k])
        if "halflife_h" in data:
            out["halflife_h"] = _clamp_halflife(data["halflife_h"])
    return out


def _news_sources_path() -> Path:
    return get_terminal_settings().data_dir / "news_sources.json"


def _clamp_max_items(value: object, default: int = 30) -> int:
    """Headline cap for the news feed — kept in a sane range (10–100)."""
    try:
        return max(10, min(100, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def get_news_sources() -> dict:
    """Saved news-source config merged over defaults (every built-in defaults enabled, weight 50)."""
    builtin = {k: True for k in _BUILTIN_NEWS_KEYS}
    builtin_weights = {k: DEFAULT_NEWS_WEIGHT for k in _BUILTIN_NEWS_KEYS}
    custom: list[dict] = []
    max_items = 30
    ranking = dict(DEFAULT_RANKING)
    p = _news_sources_path()
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                max_items = _clamp_max_items(data.get("max_items", 30))
                ranking = _merge_ranking(data.get("ranking"))
                for k, v in (data.get("builtin") or {}).items():
                    if k in builtin:
                        builtin[k] = bool(v)
                for k, v in (data.get("builtin_weights") or {}).items():
                    if k in builtin_weights:
                        builtin_weights[k] = _clamp_weight(v)
                for c in data.get("custom") or []:
                    if isinstance(c, dict) and c.get("url"):
                        custom.append({
                            "name": str(c.get("name") or "").strip() or "Custom feed",
                            "url": str(c["url"]).strip(),
                            "enabled": bool(c.get("enabled", True)),
                            "weight": _clamp_weight(c.get("weight", DEFAULT_NEWS_WEIGHT)),
                        })
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "builtin": builtin,
        "builtin_weights": builtin_weights,
        "custom": custom,
        "max_items": max_items,
        "ranking": ranking,
    }


def set_news_sources(
    builtin: dict,
    builtin_weights: dict,
    custom: list,
    max_items: int = 30,
    ranking: dict | None = None,
) -> dict:
    """Validate + persist the news-source config (custom feeds need an http(s) url; cap ~20)."""
    out_builtin = {k: True for k in _BUILTIN_NEWS_KEYS}
    for k, v in (builtin or {}).items():
        if k in out_builtin:
            out_builtin[k] = bool(v)
    out_weights = {k: DEFAULT_NEWS_WEIGHT for k in _BUILTIN_NEWS_KEYS}
    for k, v in (builtin_weights or {}).items():
        if k in out_weights:
            out_weights[k] = _clamp_weight(v)
    out_custom: list[dict] = []
    for c in (custom or [])[:20]:
        url = str((c or {}).get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        out_custom.append({
            "name": str((c or {}).get("name") or "").strip() or "Custom feed",
            "url": url,
            "enabled": bool((c or {}).get("enabled", True)),
            "weight": _clamp_weight((c or {}).get("weight", DEFAULT_NEWS_WEIGHT)),
        })
    cfg = {
        "builtin": out_builtin,
        "builtin_weights": out_weights,
        "custom": out_custom,
        "max_items": _clamp_max_items(max_items),
        "ranking": _merge_ranking(ranking),
    }
    p = _news_sources_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg), "utf-8")
    return cfg


# ── User news topics ("interest subscriptions") ───────────────────────────────────────
# Users subscribe to keyword interests (e.g. "semiconductors"); each becomes a topic that the
# Market/Macro-style news widgets can fetch (the keyword drives a Google News search feed). Persisted
# next to the news sources, separately so a partial save of one never clobbers the other.
_MAX_TOPICS = 30
_MAX_TOPIC_LABELS = 6  # a topic can carry several labels/aliases, all feeding its one query
_TOPIC_LABEL_MAX = 60
_TOPIC_QUERY_MAX = 200
_RESERVED_TOPIC_KEYS = ("market", "macro")  # built-in topic keys — user keys must not collide


def _news_topics_path() -> Path:
    return get_terminal_settings().data_dir / "news_topics.json"


def _slugify_topic(label: str) -> str:
    """Stable lowercase slug for a topic label; empty/odd labels fall back to ``topic``."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return slug or "topic"


def _clean_labels(raw: object, fallback: object = None) -> list[str]:
    """Normalize a topic's labels → de-duped, trimmed, capped list. Accepts a list (new shape)
    or a single string (legacy ``label``); when ``raw`` yields nothing (missing or an empty list,
    e.g. a Pydantic default), fall back to the legacy single ``fallback`` value."""
    vals = list(raw) if isinstance(raw, list) else ([raw] if raw is not None else [])
    out: list[str] = []
    seen: set[str] = set()
    for v in vals:
        s = str(v or "").strip()[:_TOPIC_LABEL_MAX]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
        if len(out) >= _MAX_TOPIC_LABELS:
            break
    if not out and fallback is not None:
        s = str(fallback or "").strip()[:_TOPIC_LABEL_MAX]
        if s:
            out = [s]
    return out


def get_news_topics() -> list[dict]:
    """Saved user topics ``[{key, labels, query, enabled}]`` (empty list if none/unreadable).

    Accepts the legacy single-``label`` shape on disk and upconverts it to ``labels`` transparently.
    """
    p = _news_topics_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = data.get("topics") if isinstance(data, dict) else data
    out: list[dict] = []
    if isinstance(items, list):
        for t in items:
            if not isinstance(t, dict):
                continue
            labels = _clean_labels(t.get("labels"), t.get("label"))
            query = str(t.get("query") or "").strip()
            key = str(t.get("key") or "").strip()
            if not labels or not query or not key:
                continue
            out.append({
                "key": key,
                "labels": labels,
                "query": query[:_TOPIC_QUERY_MAX],
                "enabled": bool(t.get("enabled", True)),
            })
    return out


def set_news_topics(topics: list) -> list[dict]:
    """Validate + persist user topics: each needs ≥1 label + a query; keys are re-slugged uniquely.

    A topic may carry several labels (aliases) that all feed its one query. Reserved built-in keys
    (``market``/``macro``) and duplicates get a numeric suffix so every topic stays addressable.
    Caps the list at ``_MAX_TOPICS``.
    """
    out: list[dict] = []
    used: set[str] = set(_RESERVED_TOPIC_KEYS)
    for t in (topics or [])[:_MAX_TOPICS]:
        td = t or {}
        labels = _clean_labels(td.get("labels"), td.get("label"))
        query = str(td.get("query") or "").strip()
        if not labels or not query:
            continue
        base = _slugify_topic(labels[0])  # key derives from the first label
        key = base
        n = 2
        while key in used:
            key = f"{base}-{n}"
            n += 1
        used.add(key)
        out.append({
            "key": key,
            "labels": labels,
            "query": query[:_TOPIC_QUERY_MAX],
            "enabled": bool(td.get("enabled", True)),
        })
    p = _news_topics_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"topics": out}), "utf-8")
    return out


# ── Market-data override (per-asset-class categories) ──────────────────────────────
# Users manage the market-data layer from Settings → Market Data, organised into per-asset-class
# CATEGORIES (equity, crypto). Each category carries its own data source(s), intraday cache TTL,
# daily-history window, and default symbol:
#   • crypto — one ccxt exchange id drives BOTH historical bars and the ccxt.pro realtime stream.
#   • equity — bars come from yfinance (the only real provider today); realtime comes from Alpaca
#              (IEX/SIP feed), a different source from its bars.
# Alpaca credentials stay GLOBAL (shared by the paper broker + the equity realtime stream).
# Persists as a small JSON file in the data dir, overlaid onto the env-derived TerminalSettings;
# deps.py reads it through the accessors below so a change applies live (lru_caches cleared on save).

SUPPORTED_EXCHANGES = ("kraken", "binance", "coinbase", "okx", "bybit", "kucoin")
DEFAULT_INTRADAY_TTL = 60.0   # seconds — transient intraday-bars cache lifetime
DEFAULT_HISTORY_YEARS = 3     # default daily-bars lookback when no explicit start is given
# Alpaca equity real-time stream feed: IEX (free, IEX-venue prints) or SIP (paid, full tape).
SUPPORTED_EQUITY_FEEDS = ("iex", "sip")
DEFAULT_EQUITY_FEED = "iex"

# The asset classes with real bar + realtime providers (FX/futures have none → out of scope).
MARKET_DATA_CATEGORIES = ("equity", "crypto", "rates", "fx", "commodity")
# FICC asset classes (rates futures / spot FX / commodity futures): all yfinance-sourced with no
# exchange or realtime-stream choice, so they expose only the bars knobs (history + cache + default).
FICC_CATEGORIES = ("rates", "fx", "commodity")
FICC_DEFAULT_SYMBOLS = {"rates": "ZN", "fx": "EUR/USD", "commodity": "GC"}
EQUITY_BARS_SOURCES = ("yfinance",)            # the only real equity bar provider today
EQUITY_REALTIME_SOURCES = ("auto", "alpaca", "none")   # auto = alpaca when creds exist, else off
DEFAULT_CRYPTO_EXCHANGE = "kraken"

# Pluggable order-book (L2 depth) producers, vendor-agnostic. 'sim' = a local simulated feed
# (always available, no creds — a modelled ladder around the real mid); 'none' = no depth. The real
# vendors each plug in via one build_depth_source() branch + a producer module and are gated on
# their SDK/creds (they report 'unavailable' until configured): 'ibkr' (ib_async reqMktDepth, needs
# IB Gateway), 'databento' (Live MBP-10 API key), 'dxfeed' (dxLink token). Equity + the three FICC
# classes share DEPTH_SOURCES; crypto keeps 'exchange' — its existing real ccxt.pro L2 — as default.
# The 'auto' token picks the best *available* producer at read time (services/autopick.py):
# ranked real vendors whose deep probe passes (IB Gateway reachable, Databento live-licensed),
# else 'sim' — so adding a license/gateway upgrades the book with zero clicks.
DEPTH_SOURCES = ("auto", "sim", "ibkr", "databento", "dxfeed", "none")
CRYPTO_DEPTH_SOURCES = ("auto", "exchange", "sim", "none")
DEFAULT_DEPTH_SOURCE = "auto"

# Equity-options chain source (Settings → Market Data → Options). A standalone chain subsystem
# (services/options.py), NOT a MARKET_DATA_CATEGORIES member — options are chain-shaped, not OHLCV.
# 'yfinance' = free delayed chains + IV, greeks computed locally (Black-Scholes); real vendors plug
# in via build_options_source() + a module, gated on SDK/creds ('unavailable' until configured).
# OPTIONS_CAPS drives the Settings capability note + whether the widget shows source-provided greeks.
OPTIONS_SOURCES = ("yfinance", "tradier", "polygon", "ibkr", "none")
DEFAULT_OPTIONS_SOURCE = "yfinance"
DEFAULT_OPTIONS_UNDERLYING = "AAPL"
DEFAULT_OPTIONS_EXPIRY_WINDOW = 60    # days forward to list expirations
DEFAULT_OPTIONS_CHAIN_TTL = 60.0      # seconds — chain cache lifetime
OPTIONS_CAPS = {
    "yfinance": {"chains": True, "iv": True, "greeks": False, "realtime": False},
    "tradier": {"chains": True, "iv": True, "greeks": True, "realtime": True},
    "polygon": {"chains": True, "iv": True, "greeks": True, "realtime": True},
    "ibkr": {"chains": True, "iv": True, "greeks": True, "realtime": True},
    "none": {"chains": False, "iv": False, "greeks": False, "realtime": False},
}


def _market_data_path() -> Path:
    return get_terminal_settings().data_dir / "market_data.json"


def _clamp_ttl(value: object, default: float = DEFAULT_INTRADAY_TTL) -> float:
    """Intraday cache TTL in seconds, kept sane (5s … 600s)."""
    try:
        return max(5.0, min(600.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clamp_history_years(value: object, default: int = DEFAULT_HISTORY_YEARS) -> int:
    """Default daily-history window in years, kept sane (1 … 30)."""
    try:
        return max(1, min(30, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clamp_symbol(value: object, default: str) -> str:
    """A default ticker: trimmed + upper-cased (keeps the BTC/USDT slash); blank → default."""
    s = str(value or "").strip().upper()
    return s or default


def _category_defaults() -> dict:
    """Seed per-category records from the env-derived TerminalSettings."""
    term = get_terminal_settings()
    ex = term.crypto_exchange if term.crypto_exchange in SUPPORTED_EXCHANGES else DEFAULT_CRYPTO_EXCHANGE
    return {
        "equity": {
            "bars_source": "yfinance",
            "realtime_source": "auto",             # alpaca when creds exist, else off — no knob to forget
            "realtime_feed": DEFAULT_EQUITY_FEED,
            "depth_source": DEFAULT_DEPTH_SOURCE,  # order-book (L2) producer — 'auto' picks best available
            "intraday_ttl": DEFAULT_INTRADAY_TTL,
            "history_years": DEFAULT_HISTORY_YEARS,
            "default_symbol": "AAPL",
        },
        "crypto": {
            "source": ex,                          # ccxt exchange — drives bars + realtime
            "realtime": True,                      # live ticker/book/trades on; off keeps bars/charts
            "depth_source": "exchange",            # 'exchange' = the real ccxt.pro L2 (unchanged default)
            "intraday_ttl": DEFAULT_INTRADAY_TTL,
            "history_years": DEFAULT_HISTORY_YEARS,
            "default_symbol": "BTC/USDT",
        },
        # FICC classes share one simple shape (yfinance bars; no exchange/realtime knobs, but a
        # selectable order-book depth producer like equity).
        **{
            cat: {
                "bars_source": "yfinance",
                "depth_source": DEFAULT_DEPTH_SOURCE,
                "intraday_ttl": DEFAULT_INTRADAY_TTL,
                "history_years": DEFAULT_HISTORY_YEARS,
                "default_symbol": FICC_DEFAULT_SYMBOLS[cat],
            }
            for cat in FICC_CATEGORIES
        },
        # Options: a standalone chain subsystem (not OHLCV) — a chain source + chain knobs. Kept out
        # of MARKET_DATA_CATEGORIES so the bars/depth/_category loops never touch it.
        "options": {
            "source": DEFAULT_OPTIONS_SOURCE,
            "default_underlying": DEFAULT_OPTIONS_UNDERLYING,
            "expiry_window": DEFAULT_OPTIONS_EXPIRY_WINDOW,
            "chain_ttl": DEFAULT_OPTIONS_CHAIN_TTL,
            "greeks": "auto",   # 'auto' = compute via Black-Scholes when the source lacks greeks
        },
    }


def _overlay_categories(cats: dict, saved: object) -> None:
    """Validate + overlay a ``{equity:{…}, crypto:{…}}`` map onto the default category records."""
    if not isinstance(saved, dict):
        return
    eq, cr = cats["equity"], cats["crypto"]
    se = saved.get("equity")
    if isinstance(se, dict):
        if str(se.get("bars_source") or "").strip().lower() in EQUITY_BARS_SOURCES:
            eq["bars_source"] = str(se["bars_source"]).strip().lower()
        if str(se.get("realtime_source") or "").strip().lower() in EQUITY_REALTIME_SOURCES:
            eq["realtime_source"] = str(se["realtime_source"]).strip().lower()
        if str(se.get("realtime_feed") or "").strip().lower() in SUPPORTED_EQUITY_FEEDS:
            eq["realtime_feed"] = str(se["realtime_feed"]).strip().lower()
        if str(se.get("depth_source") or "").strip().lower() in DEPTH_SOURCES:
            eq["depth_source"] = str(se["depth_source"]).strip().lower()
        if "intraday_ttl" in se:
            eq["intraday_ttl"] = _clamp_ttl(se["intraday_ttl"])
        if "history_years" in se:
            eq["history_years"] = _clamp_history_years(se["history_years"])
        if "default_symbol" in se:
            eq["default_symbol"] = _clamp_symbol(se["default_symbol"], eq["default_symbol"])
    sc = saved.get("crypto")
    if isinstance(sc, dict):
        if str(sc.get("source") or "").strip().lower() in SUPPORTED_EXCHANGES:
            cr["source"] = str(sc["source"]).strip().lower()
        if str(sc.get("depth_source") or "").strip().lower() in CRYPTO_DEPTH_SOURCES:
            cr["depth_source"] = str(sc["depth_source"]).strip().lower()
        if "realtime" in sc:
            cr["realtime"] = bool(sc["realtime"])
        if "intraday_ttl" in sc:
            cr["intraday_ttl"] = _clamp_ttl(sc["intraday_ttl"])
        if "history_years" in sc:
            cr["history_years"] = _clamp_history_years(sc["history_years"])
        if "default_symbol" in sc:
            cr["default_symbol"] = _clamp_symbol(sc["default_symbol"], cr["default_symbol"])
    # FICC categories: history / cache TTL / default symbol + a selectable depth (order-book) source.
    for cat in FICC_CATEGORIES:
        sf = saved.get(cat)
        if not isinstance(sf, dict) or cat not in cats:
            continue
        rec = cats[cat]
        if str(sf.get("depth_source") or "").strip().lower() in DEPTH_SOURCES:
            rec["depth_source"] = str(sf["depth_source"]).strip().lower()
        if "intraday_ttl" in sf:
            rec["intraday_ttl"] = _clamp_ttl(sf["intraday_ttl"])
        if "history_years" in sf:
            rec["history_years"] = _clamp_history_years(sf["history_years"])
        if "default_symbol" in sf:
            rec["default_symbol"] = _clamp_symbol(sf["default_symbol"], rec["default_symbol"])
    # Options (standalone chain subsystem): source + chain knobs.
    so = saved.get("options")
    if isinstance(so, dict) and "options" in cats:
        opt = cats["options"]
        if str(so.get("source") or "").strip().lower() in OPTIONS_SOURCES:
            opt["source"] = str(so["source"]).strip().lower()
        if "default_underlying" in so:
            opt["default_underlying"] = _clamp_symbol(so["default_underlying"], opt["default_underlying"])
        if "expiry_window" in so:
            try:
                opt["expiry_window"] = max(7, min(365, int(so["expiry_window"])))
            except (TypeError, ValueError):
                pass
        if "chain_ttl" in so:
            opt["chain_ttl"] = _clamp_ttl(so["chain_ttl"], DEFAULT_OPTIONS_CHAIN_TTL)
        if str(so.get("greeks") or "").strip().lower() in ("auto", "passthrough", "off"):
            opt["greeks"] = str(so["greeks"]).strip().lower()


def _merge_saved(cats: dict, data: dict) -> None:
    """Overlay a persisted market_data.json onto defaults — legacy flat keys + the categories map.

    Legacy (pre-categories) files stored top-level ``exchange``/``equity_feed``/``intraday_ttl``/
    ``history_years``; those seed both categories so old configs migrate transparently. The new
    per-category ``categories`` map (when present) then wins.
    """
    eq, cr = cats["equity"], cats["crypto"]
    if str(data.get("exchange") or "").strip().lower() in SUPPORTED_EXCHANGES:
        cr["source"] = str(data["exchange"]).strip().lower()
    if str(data.get("equity_feed") or "").strip().lower() in SUPPORTED_EQUITY_FEEDS:
        eq["realtime_feed"] = str(data["equity_feed"]).strip().lower()
    if "intraday_ttl" in data:
        eq["intraday_ttl"] = cr["intraday_ttl"] = _clamp_ttl(data["intraday_ttl"])
    if "history_years" in data:
        eq["history_years"] = cr["history_years"] = _clamp_history_years(data["history_years"])
    _overlay_categories(cats, data.get("categories"))


def get_market_data_config() -> dict:
    """Saved market-data config merged over the env-derived defaults.

    Shape: ``{categories: {equity:{…}, crypto:{…}}, alpaca_api_key, alpaca_api_secret,
    alpaca_paper}`` plus derived back-compat top-level keys (``exchange``/``equity_feed``/
    ``intraday_ttl``/``history_years``, the latter two taken from the equity category).
    Empty/missing fields fall back to TerminalSettings (env) values.
    """
    term = get_terminal_settings()
    cats = _category_defaults()
    creds = {
        "alpaca_api_key": term.alpaca_api_key,
        "alpaca_api_secret": term.alpaca_api_secret,
        "alpaca_paper": term.alpaca_paper,
    }
    p = _market_data_path()
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                _merge_saved(cats, data)
                # Secrets are stored encrypted (enc:…); decrypt for in-process use only.
                if data.get("alpaca_api_key"):
                    creds["alpaca_api_key"] = decrypt_secret(str(data["alpaca_api_key"]))
                if data.get("alpaca_api_secret"):
                    creds["alpaca_api_secret"] = decrypt_secret(str(data["alpaca_api_secret"]))
                if "alpaca_paper" in data:
                    creds["alpaca_paper"] = bool(data["alpaca_paper"])
        except (json.JSONDecodeError, OSError):
            pass
    eq, cr = cats["equity"], cats["crypto"]
    return {
        "categories": cats,
        **creds,
        # derived back-compat top-level keys (legacy callers / status / tests)
        "exchange": cr["source"],
        "equity_feed": eq["realtime_feed"],
        "intraday_ttl": eq["intraday_ttl"],
        "history_years": eq["history_years"],
    }


def set_market_data_config(
    exchange: str = "",
    intraday_ttl: float | None = None,
    history_years: int | None = None,
    alpaca_api_key: str = "",
    alpaca_api_secret: str = "",
    alpaca_paper: bool = True,
    equity_feed: str = "",
    categories: dict | None = None,
) -> dict:
    """Validate + persist the market-data config.

    Two input paths (may be combined): the new per-category ``categories`` map (wins per-category),
    and the legacy flat args (exchange/intraday_ttl/history_years/equity_feed) kept for back-compat
    — the flat args seed BOTH categories first, then the map overrides. A blank Alpaca key/secret
    keeps the previously saved value (so the UI can show it masked — same trick as set_llm_override).
    Returns the resolved (plaintext-creds) view, same shape as get_market_data_config().
    """
    prev = get_market_data_config()
    cats = {k: dict(v) for k, v in prev["categories"].items()}

    # legacy flat args → seed both categories. An invalid exchange keeps the current value; an
    # explicitly-provided-but-invalid feed resets to the default (preserves pre-categories semantics).
    if (exchange or "").strip().lower() in SUPPORTED_EXCHANGES:
        cats["crypto"]["source"] = exchange.strip().lower()
    feed = (equity_feed or "").strip().lower()
    if feed:
        cats["equity"]["realtime_feed"] = feed if feed in SUPPORTED_EQUITY_FEEDS else DEFAULT_EQUITY_FEED
    if intraday_ttl is not None:
        cats["equity"]["intraday_ttl"] = cats["crypto"]["intraday_ttl"] = _clamp_ttl(intraday_ttl)
    if history_years is not None:
        cats["equity"]["history_years"] = cats["crypto"]["history_years"] = _clamp_history_years(history_years)

    # new per-category map wins
    _overlay_categories(cats, categories)

    # credentials (blank keeps the previously saved value)
    key = (alpaca_api_key or "").strip() or prev.get("alpaca_api_key", "")
    secret = (alpaca_api_secret or "").strip() or prev.get("alpaca_api_secret", "")

    to_store = {
        "categories": cats,
        "alpaca_paper": bool(alpaca_paper),
        "alpaca_api_key": encrypt_secret(key),
        "alpaca_api_secret": encrypt_secret(secret),
    }
    p = _market_data_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(to_store), "utf-8")
    return get_market_data_config()  # re-read so the returned view is fully validated/back-compat


def clear_alpaca_creds() -> dict:
    """Remove the saved Alpaca key/secret entirely (not just blank-keeps-saved).

    Persists the category config + paper flag WITHOUT any credentials, so the override no longer
    supplies a key. After this the broker falls back to the local SimBroker and the equity realtime
    stream goes inert (unless an OFT_ALPACA_API_KEY env var still provides one). Returns the resolved
    config. Callers should follow with deps.reload_market_data() to drop the broker/stream caches.
    """
    prev = get_market_data_config()
    to_store = {
        "categories": {k: dict(v) for k, v in prev["categories"].items()},
        "alpaca_paper": bool(prev.get("alpaca_paper", True)),
        # alpaca_api_key / alpaca_api_secret intentionally omitted → no saved credentials
    }
    p = _market_data_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(to_store), "utf-8")
    return get_market_data_config()


# ── Market-data vendor providers (Databento / Polygon / Tradier / dxFeed / IBKR) ─────
# Credentials for the optional depth/options vendors, entered in Settings → Data Providers and stored
# encrypted in <data_dir>/providers.json (the same at-rest scheme as the Alpaca keys). The vendor
# service modules read these first, then fall back to their historical env vars, so an existing
# env-based setup keeps working and the UI is purely additive. The status view (provider_state) never
# leaks a secret — only whether one is saved (has_key) — mirroring _state()/get_market_data_config().
#
# Each provider lists its secret fields (encrypted at rest, blank-on-save keeps the saved value) and
# its plain fields (IBKR host/port, Tradier env — stored/echoed in the clear). IBKR client-ids stay
# in code (depth=17, options=18) so the two connections never collide; the UI sets only host/port.
PROVIDER_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "databento": {"secret": ("api_key",), "plain": ()},
    "polygon": {"secret": ("api_key",), "plain": ()},
    "tradier": {"secret": ("token",), "plain": ("env",)},
    "dxfeed": {"secret": ("address",), "plain": ()},
    "ibkr": {"secret": (), "plain": ("host", "port")},
}
# env-var fallbacks per provider — used only to tell the UI a value is set outside the app.
_PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    "databento": ("DATABENTO_API_KEY", "OFT_DATABENTO_API_KEY"),
    "polygon": ("POLYGON_API_KEY", "OFT_POLYGON_API_KEY"),
    "tradier": ("TRADIER_TOKEN", "OFT_TRADIER_TOKEN"),
    "dxfeed": ("OFT_DXFEED_ADDRESS",),
    "ibkr": ("OFT_IBKR_HOST", "OFT_IBKR_PORT"),
}


def _providers_path() -> Path:
    return get_terminal_settings().data_dir / "providers.json"


def _read_providers_raw() -> dict:
    p = _providers_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_provider_config() -> dict:
    """All saved vendor-provider settings, secrets DECRYPTED for in-process use.

    Shape: ``{<provider>: {<field>: <value>, ...}, ...}`` — only providers with a saved record and
    only known fields (PROVIDER_FIELDS) appear; empty fields are dropped.
    """
    raw = _read_providers_raw()
    out: dict[str, dict] = {}
    for name, spec in PROVIDER_FIELDS.items():
        rec = raw.get(name)
        if not isinstance(rec, dict):
            continue
        clean: dict[str, str] = {}
        for f in spec["secret"]:
            if rec.get(f):
                dec = decrypt_secret(str(rec[f]))
                if dec:
                    clean[f] = dec
        for f in spec["plain"]:
            if rec.get(f) not in (None, ""):
                clean[f] = str(rec[f])
        if clean:
            out[name] = clean
    return out


def get_provider_secret(name: str, field: str) -> str:
    """One saved provider field (decrypted), or "" when unset. The vendor services' fallback source."""
    return get_provider_config().get(name, {}).get(field, "") or ""


def set_provider_config(name: str, fields: dict) -> dict:
    """Validate + persist one provider's settings (secret fields encrypted).

    A blank secret keeps the previously saved value (so the UI can render it masked — the same trick
    as set_llm_override / set_market_data_config). Plain fields overwrite with the submitted value.
    Returns the decrypted get_provider_config(). Raises ValueError on an unknown provider name.
    """
    name = (name or "").strip().lower()
    spec = PROVIDER_FIELDS.get(name)
    if spec is None:
        raise ValueError(f"unknown provider '{name}'")
    raw = _read_providers_raw()
    rec = dict(raw.get(name) or {})
    for f in spec["secret"]:
        if f in fields:
            val = str(fields[f] or "").strip()
            if val:  # blank keeps the previously saved secret
                rec[f] = encrypt_secret(val)
    for f in spec["plain"]:
        if f in fields:
            rec[f] = str(fields[f] or "").strip()
    raw[name] = rec
    p = _providers_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw), "utf-8")
    return get_provider_config()


def clear_provider_secret(name: str) -> dict:
    """Remove a provider's saved record entirely (creds + settings). Returns get_provider_config()."""
    name = (name or "").strip().lower()
    raw = _read_providers_raw()
    if name in raw:
        del raw[name]
        p = _providers_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(raw), "utf-8")
    return get_provider_config()


def provider_state() -> dict:
    """Non-secret status view for Settings → Data Providers.

    Never leaks a secret — only whether one is saved (``has_key``) and whether an env-var fallback is
    currently present (``from_env``). Plain fields (IBKR host/port, Tradier env) are echoed so the UI
    can show them.
    """
    cfg = get_provider_config()

    def _env(name: str) -> bool:
        return any((os.getenv(v) or "").strip() for v in _PROVIDER_ENV.get(name, ()))

    dbt, pg, tr, dx, ib = (cfg.get(k, {}) for k in ("databento", "polygon", "tradier", "dxfeed", "ibkr"))
    return {
        "databento": {"has_key": bool(dbt.get("api_key")), "from_env": _env("databento")},
        "polygon": {"has_key": bool(pg.get("api_key")), "from_env": _env("polygon")},
        "tradier": {"has_key": bool(tr.get("token")), "env": tr.get("env", ""), "from_env": _env("tradier")},
        "dxfeed": {"has_key": bool(dx.get("address")), "from_env": _env("dxfeed")},
        "ibkr": {
            "host": ib.get("host", ""),
            "port": ib.get("port", ""),
            "configured": bool(ib.get("host") or ib.get("port")),
            "from_env": _env("ibkr"),
        },
    }


def _category(asset: str) -> dict:
    """The resolved record for an asset class, falling back to equity for unknown classes."""
    cats = get_market_data_config()["categories"]
    return cats.get((asset or "").lower(), cats["equity"])


def get_crypto_exchange() -> str:
    """The ccxt exchange id for crypto bars + realtime (override → else env default)."""
    return get_market_data_config()["categories"]["crypto"]["source"]


def get_intraday_ttl(asset: str = "equity") -> float:
    """Intraday-bars cache TTL in seconds for an asset class (override → else default)."""
    return _category(asset)["intraday_ttl"]


def get_history_years(asset: str = "equity") -> int:
    """Default daily-history window in years for an asset class (override → else default)."""
    return _category(asset)["history_years"]


def get_default_symbol(asset: str = "equity") -> str:
    """The category's default symbol — seeds the initial ticker for that asset class."""
    return _category(asset)["default_symbol"]


def get_bars_source(asset: str = "equity") -> str:
    """The historical-bars data source for an asset class ('yfinance' / a ccxt exchange id)."""
    cat = _category(asset)
    return cat.get("bars_source", cat.get("source", ""))


def get_realtime_source(asset: str = "equity") -> str:
    """The *configured* realtime source ('auto'/'alpaca'/'none' for equity, exchange for crypto)."""
    cat = _category(asset)
    return cat.get("realtime_source", cat.get("source", "none"))


def get_equity_realtime_resolved() -> str:
    """The concrete equity realtime source after resolving 'auto' ('alpaca' with creds, else 'none')."""
    src = get_realtime_source("equity")
    if src != "auto":
        return src
    return "alpaca" if get_alpaca_creds()[0] else "none"


def equity_realtime_enabled() -> bool:
    """Whether the live equity stream should run: an (auto-)resolved Alpaca source AND creds."""
    return get_equity_realtime_resolved() == "alpaca" and bool(get_alpaca_creds()[0])


def get_crypto_realtime_enabled() -> bool:
    """Whether crypto live streaming (ticker/book/trades) is on. Off keeps bars/charts working."""
    return bool(get_market_data_config()["categories"]["crypto"].get("realtime", True))


def get_alpaca_creds() -> tuple[str, str, bool]:
    """Alpaca ``(api_key, api_secret, paper)`` (override → else env)."""
    cfg = get_market_data_config()
    return cfg["alpaca_api_key"], cfg["alpaca_api_secret"], cfg["alpaca_paper"]


def get_equity_feed() -> str:
    """Alpaca equity real-time feed, 'iex' or 'sip' (override → else default)."""
    return get_market_data_config()["categories"]["equity"]["realtime_feed"]


def get_depth_source(asset: str = "equity") -> str:
    """The *configured* order-book (L2) depth producer ('auto'/'sim'/'exchange'/'none'/vendor id)."""
    return _category(asset).get("depth_source", "none")


def get_depth_source_resolved(asset: str = "equity") -> str:
    """The concrete depth producer after resolving 'auto' (services/autopick ranking + probes)."""
    src = get_depth_source(asset)
    if src != "auto":
        return src
    from app.services.autopick import resolve_depth_source  # lazy: keep config import-light

    return resolve_depth_source(asset)


def get_depth_topic_token(asset: str = "equity") -> str:
    """The realtime-hub 'exchange' segment for this asset's ``book:`` topic, '' when depth is off.

    Crypto with the 'exchange' source keeps its existing real ccxt.pro path — the token is just the
    ccxt exchange id (e.g. 'kraken', no dot → routed to the ccxt watch loop). Every other producer
    encodes vendor + asset as ``{source}.{asset}`` (e.g. 'sim.equity') so the hub can pick the right
    mid provider and symbol mapping. The frontend never builds this — it reads it from /api/health.
    """
    return _depth_token_for(asset, get_depth_source_resolved(asset))


def _depth_token_for(asset: str, src: str) -> str:
    """Derive the ``book:`` topic token from an already-resolved source (shared by depth_status)."""
    if not src or src == "none":
        return ""
    if asset == "crypto" and src == "exchange":
        return get_crypto_exchange()
    return f"{src}.{asset}"


def get_depth_enabled(asset: str = "equity") -> bool:
    """Whether order-book depth is available for an asset class right now.

    'none' → off. Crypto real L2 honors the crypto realtime toggle (so 'Realtime: off' also stops
    the book). 'sim' is always available. 'auto' resolves first, so a vendor it picked has already
    passed its availability probe; a pinned vendor still reports enabled and surfaces its own
    status frames when misconfigured.
    """
    return _depth_enabled_for(asset, get_depth_source_resolved(asset))


def _depth_enabled_for(asset: str, src: str) -> bool:
    """Derive availability from an already-resolved source (shared by depth_status)."""
    if not src or src == "none":
        return False
    if asset == "crypto" and src == "exchange":
        return get_crypto_realtime_enabled()
    return True


def depth_status(asset: str = "equity") -> dict:
    """One consistent depth-status record: ``{source, configured, token, enabled}``.

    Resolves 'auto' exactly ONCE and derives token/enabled from that same answer — the health and
    settings status blocks previously resolved three times per asset, so a probe-cache expiry (or
    a concurrent ``autopick.invalidate()``) between calls could pair one source with another
    source's token. Also 3× cheaper on a cold probe cache.
    """
    configured = get_depth_source(asset)
    src = get_depth_source_resolved(asset)
    return {
        "source": src,
        "configured": configured,
        "token": _depth_token_for(asset, src),
        "enabled": _depth_enabled_for(asset, src),
    }


# Pluggable time-&-sales (trade prints) producers for asset classes with no free real tape. 'sim' =
# a local simulated feed (modelled prints around the real mid, always available). Real vendors plug
# in via one build_trades_source() branch + a producer module (gated on SDK/creds), mirroring
# DEPTH_SOURCES. Crypto keeps 'exchange' (real ccxt.pro tape) and equity keeps 'alpaca' — both real.
TRADES_SOURCES = ("sim", "ibkr", "databento")
DEFAULT_TRADES_SOURCE = "sim"
# The equity tape token — must match services/realtime.EQUITY_SOURCE (kept a literal to avoid a
# config→realtime import cycle).
_EQUITY_TRADES_TOKEN = "alpaca"


def get_trades_source(asset: str = "equity") -> str:
    """The time-&-sales (tape) producer id for an asset class.

    Fixed per-class policy (not a user choice, unlike depth): crypto uses its real ccxt.pro tape
    ('exchange'), equity uses the Alpaca tape ('alpaca'), and the FICC classes — which have no free
    print feed — use the pluggable simulated source ('sim'). Unknown classes → 'none'.
    """
    a = (asset or "equity").lower()
    if a == "crypto":
        return "exchange"
    if a == "equity":
        return _EQUITY_TRADES_TOKEN
    if a in FICC_CATEGORIES:
        return DEFAULT_TRADES_SOURCE
    return "none"


def get_trades_enabled(asset: str = "equity") -> bool:
    """Whether a live time-&-sales tape is available for an asset class right now.

    Crypto honors the crypto-realtime toggle; equity needs an (auto-)resolved 'alpaca' realtime
    source + creds — the SAME gate as ``equity_stream`` on /api/health, so a configured 'auto'
    lights the tape exactly when it lights the stream. The simulated FICC tape is always available.
    """
    a = (asset or "equity").lower()
    if a == "crypto":
        return get_crypto_realtime_enabled()
    if a == "equity":
        return equity_realtime_enabled()
    if a in FICC_CATEGORIES:
        return True
    return False


def get_trades_topic_token(asset: str = "equity") -> str:
    """The realtime-hub 'exchange' segment for this asset's ``trades:`` topic, '' when unavailable.

    Crypto keeps its real ccxt.pro tape (token = the ccxt exchange id, no dot → the ccxt watch loop).
    Equity keeps the Alpaca tape (token = 'alpaca', no dot → the Alpaca stream). Simulated/vendor
    producers encode source + asset as ``{source}.{asset}`` (e.g. 'sim.rates') so the hub picks the
    right mid provider + symbol mapping. Empty when the tape is off. The frontend never builds this —
    it reads it from /api/health (mirrors get_depth_topic_token).
    """
    if not get_trades_enabled(asset):
        return ""
    a = (asset or "equity").lower()
    src = get_trades_source(a)
    if a == "crypto" and src == "exchange":
        return get_crypto_exchange()
    if a == "equity" and src == _EQUITY_TRADES_TOKEN:
        return _EQUITY_TRADES_TOKEN
    return f"{src}.{a}"


def _options_cfg() -> dict:
    """The options-chain config record (standalone; not a MARKET_DATA_CATEGORIES member)."""
    return get_market_data_config()["categories"].get("options", {})


def get_options_source() -> str:
    """The active options-chain source id ('yfinance'/'tradier'/'polygon'/'ibkr'/'none')."""
    return _options_cfg().get("source", DEFAULT_OPTIONS_SOURCE)


def get_options_enabled() -> bool:
    """Whether an options-chain source is selected (not 'none')."""
    src = get_options_source()
    return bool(src) and src != "none"


def get_options_caps(source: str | None = None) -> dict:
    """Capability map (chains/iv/greeks/realtime) for a source (default: the active one)."""
    return OPTIONS_CAPS.get(source or get_options_source(), OPTIONS_CAPS["none"])


def get_options_default_underlying() -> str:
    return _options_cfg().get("default_underlying", DEFAULT_OPTIONS_UNDERLYING)


def get_options_expiry_window() -> int:
    return int(_options_cfg().get("expiry_window", DEFAULT_OPTIONS_EXPIRY_WINDOW))


def get_options_chain_ttl() -> float:
    return float(_options_cfg().get("chain_ttl", DEFAULT_OPTIONS_CHAIN_TTL))


def get_options_greeks_mode() -> str:
    return _options_cfg().get("greeks", "auto")
