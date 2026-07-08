"""Backtest-proposals service: LLM proposals are validated/clamped against the live inventory,
and a deterministic template set backstops a missing/garbled LLM."""

import json

import pytest

import app.services.backtest_proposals as btp

FACTORS = {
    "momentum": "Momentum (90d)",
    "value": "Value (E/P)",
    "quality": "Quality (ROE)",
    "alpha006": "Alpha 006",
}
RESEARCH = {"my-bundle": {"name": "my-bundle", "factor": "momentum", "universe": "dow30", "mode": "long_short"}}
TRAINED = [{"name": "alpha101-gbr", "framework": "sklearn", "features": ["alpha006", "alpha012"]}]
UNIVERSES = ["dow30", "sp500", "nasdaq100"]


@pytest.fixture(autouse=True)
def _stub_scan(monkeypatch):
    monkeypatch.setattr(btp, "_factor_names", lambda store: dict(FACTORS))
    monkeypatch.setattr(btp, "_research_models", lambda store: dict(RESEARCH))
    monkeypatch.setattr(btp, "_trained_models", lambda store: list(TRAINED))
    monkeypatch.setattr(btp, "list_universes", lambda: list(UNIVERSES))


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, model=None, temperature=0.0):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_llm_proposals_validated_and_clamped():
    reply = json.dumps({"proposals": [
        {"kind": "factor", "factor": "momentum", "universe": "dow30", "mode": "long_short",
         "years": 3, "prompt": "momentum long-short on dow30, 3 years", "label": "Momentum LS", "rationale": "classic"},
        {"kind": "factor", "factor": "value", "universe": "sp500", "mode": "bogus",
         "years": 99, "prompt": "value on sp500", "label": "Value", "rationale": "cheap"},
        {"kind": "factor", "factor": "DOES_NOT_EXIST", "universe": "dow30", "mode": "long_only",
         "years": 2, "prompt": "x", "label": "bad"},
        {"kind": "model", "model": "my-bundle", "label": "Bundle", "rationale": "saved"},
        {"kind": "model", "model": "alpha101-gbr", "label": "trained", "rationale": "should drop"},
    ]})
    out = btp.design_proposals(store=None, llm=FakeLLM(reply), model="m", n=8)
    props = out["proposals"]
    factors = [p for p in props if p["kind"] == "factor"]
    models = [p for p in props if p["kind"] == "model"]

    # Every referenced factor/model/universe must exist; bogus factor + trained model are dropped.
    assert all(p["factor"] in FACTORS for p in factors)
    assert all(p["model"] in RESEARCH for p in models)
    assert "my-bundle" in [p["model"] for p in models]
    assert "alpha101-gbr" not in [p["model"] for p in models]

    # Clamping: bad mode -> long_short, years 99 -> 10.
    val = next(p for p in factors if p["factor"] == "value")
    assert val["mode"] == "long_short"
    assert val["years"] == 10
    assert val["generated"] == "llm"

    assert all(p["id"].startswith("p") for p in props)
    assert out["counts"]["factors"] == len(FACTORS)


def test_fallback_when_llm_raises():
    out = btp.design_proposals(store=None, llm=FakeLLM(RuntimeError("proxy down")), model="m", n=8)
    props = out["proposals"]
    assert len(props) >= 4
    assert all(p["generated"] == "template" for p in props)
    for p in props:
        if p["kind"] == "factor":
            assert p["factor"] in FACTORS and p["universe"] in UNIVERSES and p["mode"] in btp._MODES
        else:
            assert p["model"] in RESEARCH


def test_fallback_when_no_llm():
    out = btp.design_proposals(store=None, llm=None, model="m", n=6)
    assert out["proposals"]
    assert all(p["generated"] == "template" for p in out["proposals"])
    # inventory is returned for the selector (full, unfiltered)
    inv_f = {f["name"] for f in out["inventory"]["factors"]}
    inv_m = {m["name"] for m in out["inventory"]["models"]}
    assert {"momentum", "value"} <= inv_f
    assert {"my-bundle", "alpha101-gbr"} <= inv_m


def test_selection_scopes_to_picked_factor():
    out = btp.design_proposals(store=None, llm=FakeLLM(RuntimeError("x")), model="m", n=8, select_factors=["value"])
    props = out["proposals"]
    assert props and all(p["factor"] == "value" for p in props if p["kind"] == "factor")
    # selector inventory still lists everything regardless of the active selection
    assert {"momentum", "value"} <= {f["name"] for f in out["inventory"]["factors"]}


def test_selected_trained_model_yields_component_factor_proposals():
    # alpha101-gbr's features are alpha006 (in catalog) + alpha012 (not) -> only alpha006 is runnable
    out = btp.design_proposals(store=None, llm=None, model="m", n=8, select_models=["alpha101-gbr"])
    facs = {p["factor"] for p in out["proposals"] if p["kind"] == "factor"}
    assert facs == {"alpha006"}
