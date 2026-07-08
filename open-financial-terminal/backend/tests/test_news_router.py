"""News source router + ranking: config weights round-trip, routing dedupe, composite ranking."""

from __future__ import annotations

import json

from app import config
from app.services import news_router as nr


# ── Per-source weight config ─────────────────────────────────────────────────────────
def test_weight_roundtrip_and_clamping(tmp_path, monkeypatch):
    p = tmp_path / "news_sources.json"
    monkeypatch.setattr(config, "_news_sources_path", lambda: p)

    saved = config.set_news_sources(
        builtin={"yfinance": True, "yahoo_rss": False, "gnews_rss": True},
        builtin_weights={"yfinance": 150, "yahoo_rss": -5, "gnews_rss": "oops"},  # clamp / coerce
        custom=[{"name": "Wire", "url": "https://ex.com/rss?q={symbol}", "weight": 80}],
    )
    assert saved["builtin_weights"]["yfinance"] == 100.0  # clamped to max
    assert saved["builtin_weights"]["yahoo_rss"] == 0.0    # clamped to min
    assert saved["builtin_weights"]["gnews_rss"] == config.DEFAULT_NEWS_WEIGHT  # bad value → default
    assert saved["custom"][0]["weight"] == 80.0

    got = config.get_news_sources()
    assert got["builtin"]["yahoo_rss"] is False
    assert got["builtin_weights"]["yfinance"] == 100.0
    assert got["custom"][0]["weight"] == 80.0


def test_legacy_config_without_weights_defaults_to_neutral(tmp_path, monkeypatch):
    p = tmp_path / "news_sources.json"
    p.write_text(json.dumps({"builtin": {"yfinance": True}, "custom": [
        {"name": "Old", "url": "https://ex.com/{symbol}"}  # no weight field
    ]}), "utf-8")
    monkeypatch.setattr(config, "_news_sources_path", lambda: p)

    got = config.get_news_sources()
    assert all(w == config.DEFAULT_NEWS_WEIGHT for w in got["builtin_weights"].values())
    assert got["custom"][0]["weight"] == config.DEFAULT_NEWS_WEIGHT


# ── Routing: merge + dedupe + source-weight tagging ──────────────────────────────────
def test_news_dedupes_and_keeps_top_source_weight(monkeypatch):
    def low(_symbol):
        return [{"title": "AAPL beats earnings", "publisher": "A", "link": None, "published": 100}]

    def high(_symbol):
        return [{"title": "AAPL beats earnings!", "publisher": "B", "link": "http://x", "published": 200}]

    monkeypatch.setattr(nr, "active_sources", lambda: [("low", 30.0, low), ("high", 80.0, high)])
    nr.clear_news_cache()

    items = nr.news("AAPL")
    assert len(items) == 1                      # punctuation-only title diff → deduped
    assert items[0]["source_weight"] == 80.0    # the higher-weight source wins
    assert items[0]["source"] == "high"
    assert items[0]["link"] == "http://x"       # link backfilled from the richer entry


# ── Topic feeds: symbol-agnostic Market / Macro news ─────────────────────────────────
def test_topic_news_dedupes_and_tags_source(monkeypatch):
    from app.services import fundamentals as fa

    def fake_fetch(url, label):
        if "BUSINESS" in url:
            return [{"title": "Stocks rally to record", "publisher": label,
                     "link": None, "published": 100}]
        return [{"title": "Stocks rally to record!", "publisher": label,
                 "link": "http://x", "published": 200}]

    monkeypatch.setattr(fa, "_fetch_rss", fake_fetch)
    nr.clear_news_cache()

    items = nr.topic_news("market")
    assert len(items) == 1                                   # punctuation-only diff → deduped
    assert items[0]["source"] == "Google News Business"     # first feed's label kept
    assert items[0]["source_weight"] == config.DEFAULT_NEWS_WEIGHT
    assert items[0]["link"] == "http://x"                    # link backfilled from richer entry


def test_topic_news_unknown_category_is_empty(monkeypatch):
    monkeypatch.setattr(config, "get_news_topics", lambda: [])
    assert nr.topic_news("nope") == []


# ── User topics ("interest subscriptions") ───────────────────────────────────────────
def test_topic_config_roundtrip_and_unique_keys(tmp_path, monkeypatch):
    p = tmp_path / "news_topics.json"
    monkeypatch.setattr(config, "_news_topics_path", lambda: p)

    saved = config.set_news_topics([
        {"label": "Semiconductors", "query": "semiconductors OR chips"},
        {"label": "Semiconductors", "query": "tsmc"},   # dup label → suffixed key
        {"label": "Market", "query": "markets"},        # collides with built-in → suffixed
        {"label": "", "query": "skip me"},              # no label → dropped
    ])
    keys = [t["key"] for t in saved]
    assert keys == ["semiconductors", "semiconductors-2", "market-2"]
    assert len(keys) == len(set(keys))                  # all unique
    assert saved[0]["labels"] == ["Semiconductors"]     # legacy single label → labels list
    assert config.get_news_topics() == saved            # persisted + reloads identically


def test_topic_multi_label_roundtrip_and_expansion(tmp_path, monkeypatch):
    p = tmp_path / "news_topics.json"
    monkeypatch.setattr(config, "_news_topics_path", lambda: p)

    saved = config.set_news_topics([
        {"labels": ["Semis", "Chips", "Semis"], "query": "semiconductors OR chips"},  # dup dropped
    ])
    assert saved[0]["labels"] == ["Semis", "Chips"]     # de-duped, order preserved
    assert saved[0]["key"] == "semis"                   # key from the first label
    assert config.get_news_topics() == saved

    # available_topics expands one multi-label topic into one entry per label (same key/feed)
    monkeypatch.setattr(config, "get_news_topics", lambda: saved)
    entries = [t for t in nr.available_topics() if not t["builtin"]]
    assert [(e["key"], e["label"]) for e in entries] == [("semis", "Semis"), ("semis", "Chips")]


def test_available_topics_merges_builtins_and_enabled(monkeypatch):
    monkeypatch.setattr(config, "get_news_topics", lambda: [
        {"key": "ai", "label": "AI", "query": "artificial intelligence", "enabled": True},
        {"key": "oil", "label": "Oil", "query": "oil prices", "enabled": False},  # disabled → hidden
    ])
    topics = nr.available_topics()
    keys = [t["key"] for t in topics]
    assert keys == ["market", "macro", "ai"]            # builtins first, only enabled user topics
    assert topics[0]["builtin"] is True and topics[2]["builtin"] is False


def test_topic_news_for_user_keyword_topic(monkeypatch):
    from app.services import fundamentals as fa

    monkeypatch.setattr(config, "get_news_topics", lambda: [
        {"key": "ai", "label": "AI", "query": "artificial intelligence", "enabled": True},
    ])

    seen = {}

    def fake_fetch(url, label):
        seen["url"] = url
        return [{"title": "AI breakthrough", "publisher": label, "link": "http://a", "published": 1}]

    monkeypatch.setattr(fa, "_fetch_rss", fake_fetch)
    nr.clear_news_cache()

    items = nr.topic_news("ai")
    assert len(items) == 1
    assert items[0]["source"] == "AI"                   # user topic label tags the source
    assert "news.google.com/rss/search" in seen["url"]  # keyword → Google News search feed
    assert nr.topic_subject("ai") == "AI"               # LLM subject falls back to the label


def test_topic_news_disabled_user_topic_is_empty(monkeypatch):
    monkeypatch.setattr(config, "get_news_topics", lambda: [
        {"key": "oil", "label": "Oil", "query": "oil", "enabled": False},
    ])
    nr.clear_news_cache()
    assert nr.topic_news("oil") == []


# ── Ranking: composite score ordering ────────────────────────────────────────────────
def test_rank_news_orders_by_composite_score():
    now = 1_000_000.0
    strong = {
        "title": "AAPL soars on blowout quarter", "published": now,
        "source_weight": 90, "relevance": 0.9, "score": 0.8,
    }
    weak = {
        "title": "Weekly market wrap", "published": now - 30 * 86400,
        "source_weight": 10, "relevance": 0.1, "score": 0.0,
    }
    ranked = nr.rank_news([weak, strong], "AAPL", now=now)
    assert ranked[0]["title"].startswith("AAPL soars")
    assert ranked[0]["rank_score"] > ranked[1]["rank_score"]


def test_rank_news_tolerates_missing_signals():
    now = 1_000_000.0
    item = {"title": "Some headline"}  # no published / weight / relevance / score
    ranked = nr.rank_news([item], "AAPL", now=now)
    assert 0.0 <= ranked[0]["rank_score"] <= 1.0


# ── Customizable ranking parameters ──────────────────────────────────────────────────
def test_ranking_params_roundtrip_and_clamp(tmp_path, monkeypatch):
    p = tmp_path / "news_sources.json"
    monkeypatch.setattr(config, "_news_sources_path", lambda: p)

    saved = config.set_news_sources(
        builtin={}, builtin_weights={}, custom=[],
        ranking={"recency": 2.0, "source": -1, "relevance": "x", "halflife_h": 999},
    )
    rk = saved["ranking"]
    assert rk["recency"] == 1.0   # clamped to [0, 1]
    assert rk["source"] == 0.0    # clamped to [0, 1]
    assert rk["relevance"] == config.DEFAULT_RANKING["relevance"]  # bad value → default
    assert rk["halflife_h"] == 168.0  # clamped to the 7-day max

    assert config.get_news_sources()["ranking"]["recency"] == 1.0


def test_rank_news_respects_custom_params():
    now = 1_000_000.0
    recent = {"title": "market wrap", "published": now, "source_weight": 50, "relevance": 0.0, "score": 0.0}
    old_relevant = {
        "title": "company acquires rival", "published": now - 5 * 86400,
        "source_weight": 50, "relevance": 1.0, "score": 0.9,
    }
    base = {"source": 0, "sentiment": 0, "match": 0, "halflife_h": 18}

    # recency-only → the fresh headline wins
    rec = nr.rank_news([old_relevant, recent], "ZZZ", now=now, params={**base, "recency": 1, "relevance": 0})
    assert rec[0]["title"] == "market wrap"

    # relevance-only → the older but highly-relevant headline wins
    rel = nr.rank_news([old_relevant, recent], "ZZZ", now=now, params={**base, "recency": 0, "relevance": 1})
    assert rel[0]["title"] == "company acquires rival"
