"""News source router + ranking.

The *router* fans a symbol out to every active news source (enabled built-ins + user RSS feeds,
each carrying a priority weight from Settings → News Sources), merges + dedupes the headlines, then
*ranks* them with a composite score rather than plain recency.

Ranking blends five signals (tunable weights below):

* recency       — half-life decay on the headline's age
* source weight — the per-source priority the user set (0–100)
* relevance     — how important the headline is to the stock (0–1, scored by the LLM)
* sentiment     — conviction = |sentiment score| (the LLM already returns this)
* symbol match  — whether the title mentions the ticker

Relevance is folded into the **same** structured LLM call that scores sentiment, so ranking adds no
extra round-trips: one batched, headline-cached call yields ``{sentiment, score, relevance}``.

Low-level fetchers (RSS/yfinance parsing) live in :mod:`app.services.fundamentals`; this module
imports them so it can stay focused on routing + scoring.
"""

from __future__ import annotations

import html
import re
import time
from threading import Lock
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from qhfi.research.client import LLMClient

from app import config
from app.services import fundamentals as fa
from app.services.fundamentals import (
    _custom_source,
    _epoch,  # noqa: F401 - re-exported for callers/tests that need timestamp parsing
    _gnews_rss,
    _norm,
    _yahoo_rss,
    _yf_news,
)

# Built-in keyless feeds, keyed for the Settings → News Sources toggles.
_BUILTIN_SOURCES: dict[str, tuple[str, Any]] = {
    "yfinance": ("Yahoo Finance (yfinance)", _yf_news),
    "yahoo_rss": ("Yahoo Finance RSS", _yahoo_rss),
    "gnews_rss": ("Google News RSS", _gnews_rss),
}


def builtin_source_meta() -> list[dict]:
    """[{key, label}] for the settings UI (fixed order)."""
    return [{"key": k, "label": label} for k, (label, _) in _BUILTIN_SOURCES.items()]


# ── Topic feeds (symbol-agnostic topic news widgets) ──────────────────────────────────
# Unlike the per-symbol sources above, these are fixed topic feeds: broad market/business
# headlines and macroeconomic news. They are the two *built-in* topics; users add their own
# keyword "interest" topics (persisted via config.get_news_topics) that drive a Google News
# search feed. Both feed the generic Topic News widget via /news/topic, reusing the same
# dedupe + sentiment + ranking pipeline.
_TOPIC_FEEDS: dict[str, list[tuple[str, str]]] = {
    "market": [
        ("Google News Business",
         "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ],
    "macro": [
        ("Google News Macro",
         "https://news.google.com/rss/search?q=Federal+Reserve+OR+inflation+OR+GDP"
         "+OR+interest+rates+OR+jobs+report&hl=en-US&gl=US&ceid=US:en"),
    ],
}

# Built-in topic labels (fixed order) for the topic list / command bar.
_BUILTIN_TOPIC_META: list[dict] = [
    {"key": "market", "label": "Market"},
    {"key": "macro", "label": "Macro"},
]

# Subject handed to the LLM sentiment prompt (in place of a ticker) so scoring stays meaningful.
_TOPIC_SUBJECTS: dict[str, str] = {
    "market": "the overall stock market",
    "macro": "the macroeconomy and financial markets",
}


def _topic_query_url(query: str) -> str:
    """Google News search RSS URL for a user's keyword interest (mirrors :func:`_gnews_rss`)."""
    q = httpx.QueryParams({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return f"https://news.google.com/rss/search?{q}"


def _user_topic(category: str) -> dict | None:
    """The enabled user topic with this key, or ``None``."""
    for t in config.get_news_topics():
        if t.get("key") == category and t.get("enabled", True):
            return t
    return None


def _topic_first_label(t: dict) -> str:
    """A user topic's primary (first) label, with safe fallbacks."""
    labels = t.get("labels") or ([t["label"]] if t.get("label") else [])
    return labels[0] if labels else t.get("key", "topic")


def _topic_feeds(category: str) -> list[tuple[str, str]] | None:
    """Resolve a category to ``[(label, url)]``: built-in feeds, else an enabled user topic's
    keyword feed, else ``None`` (unknown/disabled)."""
    if category in _TOPIC_FEEDS:
        return _TOPIC_FEEDS[category]
    t = _user_topic(category)
    if t:
        return [(_topic_first_label(t), _topic_query_url(t["query"]))]
    return None


def available_topics() -> list[dict]:
    """``[{key, label, builtin}]`` for every selectable topic: built-ins + enabled user topics.

    A multi-label user topic is expanded to one entry *per label* — each opens the SAME underlying
    feed (same ``key``/query) under a different display name in the launcher.
    """
    out = [{**m, "builtin": True} for m in _BUILTIN_TOPIC_META]
    for t in config.get_news_topics():
        if t.get("enabled", True):
            for label in (t.get("labels") or [_topic_first_label(t)]):
                out.append({"key": t["key"], "label": label, "builtin": False})
    return out


def topic_subject(category: str) -> str:
    """LLM-prompt subject for a topic (built-in subject, else the user topic's first label/key)."""
    if category in _TOPIC_SUBJECTS:
        return _TOPIC_SUBJECTS[category]
    t = _user_topic(category)
    return _topic_first_label(t) if t else category


def active_sources() -> list[tuple[str, float, Any]]:
    """Routing table: ``(source_key, weight, fetch_fn)`` for every enabled source.

    Built-ins use their saved enable flag + weight; custom feeds add one entry each.
    """
    cfg = config.get_news_sources()
    enabled = cfg.get("builtin", {})
    weights = cfg.get("builtin_weights", {})
    table: list[tuple[str, float, Any]] = []
    for key, (_, fn) in _BUILTIN_SOURCES.items():
        if enabled.get(key, True):
            table.append((key, float(weights.get(key, config.DEFAULT_NEWS_WEIGHT)), fn))
    for c in cfg.get("custom", []):
        if c.get("enabled", True) and c.get("url"):
            name = c.get("name", "") or "Custom feed"
            weight = float(c.get("weight", config.DEFAULT_NEWS_WEIGHT))
            table.append((name, weight, _custom_source(name, c["url"])))
    return table


# Short cache so multiple widgets / rapid polls on one symbol don't re-hit every feed.
_NEWS_TTL = 20.0
_news_cache: dict[str, tuple[float, list[dict]]] = {}
_news_lock = Lock()


def clear_news_cache() -> None:
    """Drop the per-symbol cache so a news-source config change shows immediately."""
    with _news_lock:
        _news_cache.clear()


# ── Live RSS discovery (Settings → News Sources search) ──────────────────────────────
# Pulls feed <link> tags out of a page's <head>; the existing ET.fromstring parses XML,
# not arbitrary HTML, so a tolerant regex is the lightweight (dep-free) choice here.
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""(\w[\w-]*)\s*=\s*["']([^"']*)["']""")
_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json")
# Conventional feed paths to probe when a page exposes no <link rel="alternate"> hints
# (or can't be fetched at all because it's bot-protected). Each is validated by actually
# fetching it, so a guess that isn't a feed is silently dropped.
_FALLBACK_PATHS = (
    "/feed", "/feed/", "/rss", "/rss/", "/rss.xml", "/feed.xml",
    "/index.xml", "/atom.xml", "/feeds/posts/default",
)
_DISCOVER_CAP = 8


def _query_to_url(query: str) -> str:
    """Best-effort: a full URL stays as-is, a domain gets https://, a bare word → .com."""
    q = query.strip()
    if q.lower().startswith(("http://", "https://")):
        return q
    if "." in q and " " not in q:
        return f"https://{q}"
    return f"https://{q.lower().replace(' ', '')}.com"


def _parse_feed_links(html: str, base_url: str) -> list[dict]:
    """Extract feed candidates from <link rel="alternate" type="...rss/atom..."> tags."""
    found: list[dict] = []
    for tag in _LINK_TAG_RE.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        if "alternate" not in attrs.get("rel", "").lower():
            continue
        if attrs.get("type", "").lower() not in _FEED_TYPES:
            continue
        href = attrs.get("href", "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        title = html.unescape((attrs.get("title") or "").strip()) or (urlsplit(url).hostname or "Feed")
        found.append({"title": title, "url": url})
    return found


def _probe_hosts(host: str) -> list[str]:
    """The host plus a ``feeds.<domain>`` sibling — many sites serve RSS off that subdomain."""
    host = host.lower().lstrip(".")
    hosts = [host]
    bare = host[4:] if host.startswith("www.") else host
    feeds_host = f"feeds.{bare}"
    if feeds_host != host:
        hosts.append(feeds_host)
    return hosts


def discover_feeds(query: str) -> list[dict]:
    """Best-effort live RSS discovery for a keyword or site → ``[{title, url}]``.

    Fetches the page and sniffs its ``<link rel="alternate">`` feed hints, then probes a set
    of conventional feed paths (on the host and a ``feeds.<domain>`` sibling), keeping only
    paths that actually parse as a feed. The probe runs even when the homepage itself is
    unreachable/bot-protected, since feed endpoints often aren't behind the same wall.
    Defensive throughout: a dead/slow site yields ``[]`` rather than raising.
    """
    if not query.strip():
        return []
    base = _query_to_url(query)
    candidates: list[dict] = []
    host = urlsplit(base).hostname or query.strip()
    scheme = urlsplit(base).scheme or "https"
    try:
        r = httpx.get(base, timeout=6.0, headers={"User-Agent": fa._UA}, follow_redirects=True)
        host = urlsplit(str(r.url)).hostname or host
        candidates = _parse_feed_links(r.text, str(r.url))
    except Exception:  # noqa: BLE001 - unreachable homepage; fall through to path probing
        pass

    if len(candidates) < _DISCOVER_CAP:
        for h in _probe_hosts(host):
            for path in _FALLBACK_PATHS:
                probe = f"{scheme}://{h}{path}"
                try:
                    if fa._fetch_rss(probe, h):  # only keep paths that really return a feed
                        candidates.append({"title": f"{h}{path}", "url": probe})
                except Exception:  # noqa: BLE001
                    continue

    seen: set[str] = set()
    out: list[dict] = []
    for c in candidates:
        key = c["url"].rstrip("/").lower()  # collapse /feed and /feed/ duplicates
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= _DISCOVER_CAP:
            break
    return out


def news(symbol: str, limit: int | None = 30) -> list[dict]:
    """Route to every active source, merge + dedupe by title, tag each item with its source weight.

    Items stay sorted newest-first here (the cheap default); :func:`rank_news` re-orders them by the
    composite score once sentiment/relevance have been applied. ``limit=None`` returns the full
    deduped pool (used by the /news endpoint so it can rank the whole pool before truncating).
    """
    key = symbol.upper()
    now = time.monotonic()
    with _news_lock:
        hit = _news_cache.get(key)
        if hit and now - hit[0] < _NEWS_TTL:
            pool = hit[1]
            return [dict(it) for it in (pool if limit is None else pool[:limit])]

    merged: dict[str, dict] = {}
    for src_key, weight, fetch in active_sources():
        for it in fetch(symbol):
            k = _norm(it["title"])
            if not k:
                continue
            cur = merged.get(k)
            if cur is None:
                merged[k] = {**it, "source": src_key, "source_weight": weight}
            else:
                # keep the richer entry; a headline surfaced by several feeds takes the top weight
                if not cur.get("link") and it.get("link"):
                    cur["link"] = it["link"]
                if cur.get("published") is None and it.get("published") is not None:
                    cur["published"] = it["published"]
                if weight > cur.get("source_weight", 0):
                    cur["source_weight"] = weight
                    cur["source"] = src_key

    out = sorted(merged.values(), key=lambda x: x.get("published") or 0, reverse=True)
    with _news_lock:
        _news_cache[key] = (now, out)
    return [dict(it) for it in (out if limit is None else out[:limit])]


def topic_news(category: str, limit: int | None = 30) -> list[dict]:
    """Fetch a topic category (built-in ``market``/``macro`` or a user keyword topic), merge + dedupe.

    Symbol-agnostic sibling of :func:`news`: routes to the category's topic feeds instead of
    per-symbol sources, tagging each item with its feed label and the neutral source weight. Items
    stay newest-first; :func:`rank_news` re-orders by composite score once scores are applied. Shares
    the per-symbol cache under a ``topic:<category>`` key. Unknown/disabled categories yield ``[]``.
    """
    feeds = _topic_feeds(category)
    if not feeds:
        return []
    cache_key = f"topic:{category}"
    now = time.monotonic()
    with _news_lock:
        hit = _news_cache.get(cache_key)
        if hit and now - hit[0] < _NEWS_TTL:
            pool = hit[1]
            return [dict(it) for it in (pool if limit is None else pool[:limit])]

    merged: dict[str, dict] = {}
    for label, url in feeds:
        for it in fa._fetch_rss(url, label):
            k = _norm(it["title"])
            if not k:
                continue
            cur = merged.get(k)
            if cur is None:
                merged[k] = {**it, "source": label, "source_weight": config.DEFAULT_NEWS_WEIGHT}
            else:
                # keep the richer entry when a headline shows up in more than one feed
                if not cur.get("link") and it.get("link"):
                    cur["link"] = it["link"]
                if cur.get("published") is None and it.get("published") is not None:
                    cur["published"] = it["published"]

    out = sorted(merged.values(), key=lambda x: x.get("published") or 0, reverse=True)
    with _news_lock:
        _news_cache[cache_key] = (now, out)
    return [dict(it) for it in (out if limit is None else out[:limit])]


# ── Sentiment + relevance scoring ──────────────────────────────────────────────────
_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sentiment": {"type": "string", "enum": ["bullish", "neutral", "bearish"]},
                    "score": {"type": "number"},
                    "relevance": {"type": "number"},
                },
                "required": ["title", "sentiment", "score", "relevance"],
            },
        }
    },
    "required": ["items"],
}


def score_headlines(llm: LLMClient, symbol: str, headlines: list[str]) -> list[dict]:
    """One structured LLM call → per-headline sentiment, conviction score, and relevance."""
    if not headlines:
        return []
    numbered = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headlines))
    system = (
        "You are a financial news analyst. For each headline about the given stock, return: "
        "(1) sentiment — its likely impact on the stock: bullish, neutral, or bearish; "
        "(2) score — a number in [-1, 1] (-1 very bearish, +1 very bullish); "
        "(3) relevance — a number in [0, 1] for how directly important the headline is to THIS "
        "stock (1 = company-specific material news, 0 = unrelated/marginal). "
        "Return one item per headline, preserving order."
    )
    user = f"Stock: {symbol}\nHeadlines:\n{numbered}"
    try:
        out = llm.structured(system, user, _SCORE_SCHEMA)
        return out.get("items", [])
    except Exception:  # noqa: BLE001 - LLM/proxy down shouldn't break the news feed
        return []


# Score cache: headline-normalized → {sentiment, score, relevance}. Lets the feed poll often
# while only paying the LLM for headlines it hasn't seen before.
_SCORE_CAP = 2000
_SCORE_BATCH = 24  # cap titles per LLM call so a flood of new headlines stays one prompt
_score_cache: dict[str, dict] = {}
_score_lock = Lock()


def apply_scores(llm: LLMClient, symbol: str, items: list[dict]) -> None:
    """Annotate items in place with cached sentiment/score/relevance; score only new headlines."""
    with _score_lock:
        todo = [it for it in items if _norm(it["title"]) not in _score_cache][:_SCORE_BATCH]
    if todo:
        scored = score_headlines(llm, symbol, [it["title"] for it in todo])
        by_title = {s.get("title"): s for s in scored}
        with _score_lock:
            for idx, it in enumerate(todo):
                s = scored[idx] if idx < len(scored) else by_title.get(it["title"])
                if s and s.get("sentiment"):
                    _score_cache[_norm(it["title"])] = {
                        "sentiment": s.get("sentiment"),
                        "score": s.get("score"),
                        "relevance": s.get("relevance"),
                    }
            if len(_score_cache) > _SCORE_CAP:  # drop oldest insertions
                for k in list(_score_cache)[: len(_score_cache) - _SCORE_CAP]:
                    _score_cache.pop(k, None)
    with _score_lock:
        for it in items:
            c = _score_cache.get(_norm(it["title"]))
            it["sentiment"] = c["sentiment"] if c else None
            it["score"] = c["score"] if c else None
            it["relevance"] = c["relevance"] if c else None


def clear_score_cache() -> None:
    """Test/maintenance helper — forget all cached sentiment/relevance scores."""
    with _score_lock:
        _score_cache.clear()


# ── Ranking ────────────────────────────────────────────────────────────────────────
# The composite weights + recency half-life are user-tunable (Settings → News Sources →
# Ranking); defaults live in `config.DEFAULT_RANKING`. Pass `params` to override per call
# (the /news endpoint passes the user's saved values; tests pass explicit ones).


def _recency(published: Any, now: float, halflife_h: float) -> float:
    """Half-life decay in [0, 1]; missing/old timestamps degrade toward 0."""
    ts = published if isinstance(published, (int, float)) else None
    if not ts:
        return 0.0
    age_h = max(0.0, (now - float(ts)) / 3600.0)
    return 0.5 ** (age_h / max(halflife_h, 0.1))


def rank_score(item: dict, symbol: str, now: float, params: dict | None = None) -> float:
    """Composite [0, 1] score; signal terms are blended by `params`' weights, then normalised.

    Normalising by the weight sum means only the *ratios* of the weights matter and the score
    stays in [0, 1] no matter what magnitudes the user picks.
    """
    p = params or config.DEFAULT_RANKING
    w = {k: max(0.0, float(p.get(k, config.DEFAULT_RANKING[k]))) for k in config._RANKING_WEIGHT_KEYS}
    total = sum(w.values()) or 1.0

    source = float(item.get("source_weight") or config.DEFAULT_NEWS_WEIGHT) / 100.0
    relevance = item.get("relevance")
    relevance = max(0.0, min(1.0, float(relevance))) if isinstance(relevance, (int, float)) else 0.0
    score = item.get("score")
    conviction = min(1.0, abs(float(score))) if isinstance(score, (int, float)) else 0.0
    title = (item.get("title") or "").lower()
    match = 1.0 if symbol and symbol.lower() in title else 0.0
    recency = _recency(item.get("published"), now, float(p.get("halflife_h", 18.0)))

    blended = (
        w["recency"] * recency
        + w["source"] * source
        + w["relevance"] * relevance
        + w["sentiment"] * conviction
        + w["match"] * match
    )
    return blended / total


def rank_news(
    items: list[dict], symbol: str, now: float | None = None, params: dict | None = None
) -> list[dict]:
    """Annotate each item with ``rank_score`` and return the list sorted best-first."""
    t = time.time() if now is None else now
    p = params or config.DEFAULT_RANKING
    for it in items:
        it["rank_score"] = round(rank_score(it, symbol, t, p), 4)
    return sorted(items, key=lambda x: x.get("rank_score") or 0.0, reverse=True)
