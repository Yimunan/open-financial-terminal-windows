"""Offline tests for universe.search — symbol-search ranking, dedupe, prefix-priority, EDGAR fold-in.

No filesystem/YAML/network: the three IO seams are monkeypatched at module level. list_universes
returns canned names; get_universe (lru_cached — patched by NAME on the module to bypass the cache)
returns a fake Universe whose .instruments are SimpleNamespaces; listings.search_cached returns a
canned hit list. We pin: empty/whitespace -> []; case-insensitive substring match over instrument
ids; first-universe-wins dedupe across universes; prefix matches rank before mid-string matches then
alphabetical; setdefault fold-in (static universe wins on id collision); and the limit.

Run: .venv/Scripts/python.exe -m pytest tests/test_universe_search.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import listings as ls
from app.services import universe as uni


def _ins(sym, asset="equity", sector="Tech"):
    """A fake instrument: only .id, .asset_class.value and .sector are touched by search()."""
    return SimpleNamespace(id=sym, asset_class=SimpleNamespace(value=asset), sector=sector)


def _fake_universe(*instruments):
    return SimpleNamespace(instruments=list(instruments))


@pytest.fixture
def stub_seams(monkeypatch):
    """Seal all three IO seams. Tests reconfigure the per-universe map and the EDGAR hits.

    Returns a config object whose `.universes` (dict name -> fake Universe) and `.edgar`
    (list of hit dicts) tests mutate before calling uni.search().
    """
    cfg = SimpleNamespace(universes={}, edgar=[])

    monkeypatch.setattr(uni, "list_universes", lambda: list(cfg.universes.keys()))
    # get_universe is lru_cached in the module; patching the NAME on the module bypasses the cache.
    monkeypatch.setattr(uni, "get_universe", lambda name: cfg.universes[name])
    # search() calls get_terminal_settings().data_dir only to feed search_cached (which we stub).
    monkeypatch.setattr(uni, "get_terminal_settings", lambda: SimpleNamespace(data_dir="<stub>"))
    # search() does `from app.services import listings as ls` locally, so patch the real attr.
    monkeypatch.setattr(ls, "search_cached", lambda data_dir, q: list(cfg.edgar))
    return cfg


def test_search_empty_query_returns_empty(stub_seams):
    stub_seams.universes = {"u1": _fake_universe(_ins("AAPL"))}
    assert uni.search("") == []
    assert uni.search("   ") == []


def test_search_matches_substring_case_insensitive(stub_seams):
    stub_seams.universes = {
        "u1": _fake_universe(_ins("AAPL", asset="equity", sector="Tech"), _ins("MSFT")),
    }
    hits = uni.search("aap")
    assert [h["symbol"] for h in hits] == ["AAPL"]
    hit = hits[0]
    assert hit == {"symbol": "AAPL", "asset": "equity", "sector": "Tech", "universe": "u1"}

    # mid-string, lowercased query still matches
    assert [h["symbol"] for h in uni.search("sf")] == ["MSFT"]


def test_search_dedupes_across_universes(stub_seams):
    # AAPL appears in both u1 and u2; first universe (u1) wins.
    stub_seams.universes = {
        "u1": _fake_universe(_ins("AAPL", sector="FromU1")),
        "u2": _fake_universe(_ins("AAPL", sector="FromU2"), _ins("AMZN")),
    }
    hits = uni.search("a")
    by_sym = {h["symbol"]: h for h in hits}
    assert by_sym["AAPL"]["universe"] == "u1"
    assert by_sym["AAPL"]["sector"] == "FromU1"
    # AAPL listed once only
    assert [h["symbol"] for h in hits].count("AAPL") == 1
    assert set(by_sym) == {"AAPL", "AMZN"}


def test_search_prefix_ranks_before_midmatch(stub_seams):
    # Query "AR": ARKK (prefix) should rank before BAR (mid-string), regardless of alpha order.
    # Also pin the alpha tiebreak within each bucket.
    stub_seams.universes = {
        "u1": _fake_universe(
            _ins("BAR"),    # mid-string match (B before A alphabetically, but mid-string)
            _ins("ARKK"),   # prefix match
            _ins("ART"),    # prefix match
            _ins("CAR"),    # mid-string match
        ),
    }
    hits = [h["symbol"] for h in uni.search("ar")]
    # prefix bucket first (alpha), then mid-string bucket (alpha)
    assert hits == ["ARKK", "ART", "BAR", "CAR"]


def test_search_folds_in_edgar_listings(stub_seams):
    # KARD is a fresh EDGAR listing not in any universe; AAPL collides with the static universe and
    # must NOT be overwritten by the EDGAR copy (setdefault: static wins).
    stub_seams.universes = {
        "u1": _fake_universe(_ins("AAPL", sector="StaticTech", asset="equity")),
    }
    stub_seams.edgar = [
        {"symbol": "KARD", "asset": "equity", "sector": None,
         "universe": "new listing", "name": "KARDIGAN, INC."},
        {"symbol": "AAPL", "asset": "equity", "sector": None,
         "universe": "new listing", "name": "EDGAR DUP"},
    ]
    hits = uni.search("a")
    by_sym = {h["symbol"]: h for h in hits}
    assert set(by_sym) == {"AAPL", "KARD"}
    # static universe entry preserved on collision
    assert by_sym["AAPL"]["universe"] == "u1"
    assert by_sym["AAPL"]["sector"] == "StaticTech"
    # fresh EDGAR hit folded in verbatim
    assert by_sym["KARD"]["universe"] == "new listing"
    assert by_sym["KARD"]["name"] == "KARDIGAN, INC."


def test_search_respects_limit(stub_seams):
    stub_seams.universes = {
        "u1": _fake_universe(*[_ins(f"A{i:03d}") for i in range(10)]),
    }
    hits = uni.search("a", limit=3)
    assert len(hits) == 3
    # limit applies AFTER the prefix/alpha sort -> the 3 alphabetically-first prefix matches
    assert [h["symbol"] for h in hits] == ["A000", "A001", "A002"]
