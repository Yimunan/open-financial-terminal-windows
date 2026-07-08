"""Offline tests for the SectorResearchAgent: deterministic quant evidence + a mocked LLM
(httpx MockTransport) so no network/proxy is touched."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pandas as pd
import pytest

from qhfi.api.client import ManagedClient, ManagedHttpClient
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.research.agents.sector import SectorResearchAgent, SectorResearchNote
from qhfi.research.agents.sector_context import (
    DEFAULT_PROFILE,
    SECTOR_PROFILES,
    augment_system,
    context_block,
    profile_for,
)
from qhfi.research.client import LLMClient

TECH = ["T0", "T1", "T2"]
HEALTH = ["H0", "H1", "H2"]


@pytest.fixture
def universe() -> Universe:
    def mk(tid: str, sector: str) -> Instrument:
        return Instrument(id=tid, asset_class=AssetClass.EQUITY, equity={"gics_sector": sector})
    return Universe(
        name="t",
        instruments=[mk(t, "Tech") for t in TECH] + [mk(h, "Health") for h in HEALTH],
    )


@pytest.fixture
def prices() -> pd.DataFrame:
    # Distinct drift + per-name oscillation → each name has a different realized vol, so the
    # low-vol z-score is well-defined (constant drift would give zero vol → NaN z-scores).
    dates = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    t = np.arange(200)
    drifts = [0.002, 0.001, 0.0, -0.001, 0.0005, 0.0015]
    data = {
        tk: 100 * np.cumprod(1 + d + 0.01 * np.sin(t / (3 + i)))
        for i, (tk, d) in enumerate(zip(TECH + HEALTH, drifts))
    }
    return pd.DataFrame(data, index=dates)


def _mock_agent(note: dict) -> SectorResearchAgent:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(note)}}]})
    http = ManagedHttpClient("http://x/v1", transport=httpx.MockTransport(handler),
                             managed=ManagedClient(backoff_base=0.0))
    return SectorResearchAgent(client=LLMClient(http=http))


def _note(sector: str, long_t: str, short_t: str) -> dict:
    pick = lambda tk, side: {"ticker": tk, "side": side, "thesis": f"{tk} {side} thesis",
                             "key_driver": "momentum"}
    return {
        "sector": sector,
        "regime_view": "risk-on",
        "longs": [pick(long_t, "long")],
        "shorts": [pick(short_t, "short")],
        "risks": ["crowding"],
        "hypotheses": [{
            "title": "sector momentum", "rationale": "trend persists",
            "signal_description": "rank 90d return", "universe_hint": "sector large-caps",
            "expected_edge": "fails in sharp reversals",
        }],
    }


def test_evidence_covers_only_sector_names_and_is_ranked(prices, universe):
    # evidence() is pure quant — no LLM call — so the mock client is never exercised here.
    ev = _mock_agent(_note("Tech", "T0", "T2")).evidence("Tech", prices, universe)
    assert ev.n_names == 3
    assert {r["ticker"] for r in ev.names} == set(TECH)        # only the sector's names
    comps = [r["composite"] for r in ev.names]
    assert comps == sorted(comps, reverse=True)                # ranked desc by composite
    assert set(ev.factor_ic) == {"momentum", "lowvol", "reversal"}


def test_research_returns_note_and_merges_quant_scores(prices, universe):
    agent = _mock_agent(_note("Tech", "T0", "T2"))
    note = agent.research("Tech", prices, universe)
    assert isinstance(note, SectorResearchNote)
    assert note.sector == "Tech"
    assert note.longs[0].ticker == "T0" and note.shorts[0].ticker == "T2"
    # composite_score is merged from deterministic evidence, not left to the LLM
    scores = agent.evidence("Tech", prices, universe).composite_by_ticker()
    assert note.longs[0].composite_score == pytest.approx(scores["T0"])
    assert note.shorts[0].composite_score == pytest.approx(scores["T2"])
    assert note.hypotheses and note.hypotheses[0].title


def test_evidence_rejects_thin_sector(prices, universe):
    with pytest.raises(ValueError):
        _mock_agent(_note("x", "a", "b")).evidence("Nonexistent", prices, universe)


# ── context engineering ────────────────────────────────────────────────────────
def test_profile_for_resolves_canonical_alias_and_default():
    assert len(SECTOR_PROFILES) == 11
    assert profile_for("Information Technology") is SECTOR_PROFILES["Information Technology"]
    assert profile_for("Tech") is SECTOR_PROFILES["Information Technology"]   # alias
    assert profile_for("Health") is SECTOR_PROFILES["Health Care"]           # alias
    assert profile_for("Utilities ") is SECTOR_PROFILES["Utilities"]         # whitespace/case
    assert profile_for("Nonexistent Sector") is DEFAULT_PROFILE


def test_augment_system_and_context_block_are_sector_specific():
    tech_sys = augment_system(SectorResearchAgent.SYSTEM, "Information Technology")
    util_sys = augment_system(SectorResearchAgent.SYSTEM, "Utilities")
    assert "Information Technology sector specialist" in tech_sys
    assert tech_sys != util_sys
    block = context_block("Energy")
    assert "Sector context — Energy" in block
    assert "oil" in block.lower()  # an Energy-specific driver leaked into the context


def test_research_injects_sector_context_into_the_llm_request(prices, universe):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(_note("Tech", "T0", "T2"))}}]})

    http = ManagedHttpClient("http://x/v1", transport=httpx.MockTransport(handler),
                             managed=ManagedClient(backoff_base=0.0))
    SectorResearchAgent(client=LLMClient(http=http)).research("Tech", prices, universe)

    system, user = (m["content"] for m in captured["body"]["messages"])
    assert "sector specialist" in system.lower()
    assert "Sector context — Tech" in user           # curated block prepended
    assert "secular" in user.lower()                 # a Tech-specific driver
    assert "composite" in user.lower()               # the deterministic evidence still follows
