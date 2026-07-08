"""Catalog-wiring tests for the factor library exposed to the screener + backtester.

Pure/deterministic (no network): they pin that every catalog factor is correctly classified and
that the new Book-to-Price value factor is wired end to end without colliding with E/P value.
"""

from __future__ import annotations

from app.services import factors as fac


def test_value_bp_is_registered_as_a_value_factor():
    assert "value_bp" in fac.CATALOG
    spec = fac.CATALOG["value_bp"]
    assert spec["kind"] == "value"            # routed through the fundamentals branch
    assert spec["direction"] == "cheap=long"  # higher book yield = cheaper = long


def test_value_bp_uses_book_value_per_share():
    # E/P and B/P are distinct value definitions backed by different PIT metrics.
    assert fac._FUNDAMENTAL_METRIC["value"] == "eps_ttm"
    assert fac._FUNDAMENTAL_METRIC["value_bp"] == "book_value_per_share"


def test_quality_gp_is_a_distinct_quality_factor():
    assert "quality_gp" in fac.CATALOG
    assert fac.CATALOG["quality_gp"]["kind"] == "quality"
    # ROE and gross-profitability are distinct quality signals backed by different metrics.
    assert fac._FUNDAMENTAL_METRIC["quality"] == "roe"
    assert fac._FUNDAMENTAL_METRIC["quality_gp"] == "gross_margin"


def test_value_bp_listed_for_the_ui():
    keys = {f["key"] for f in fac.available_factors()}
    assert {"value", "value_bp", "quality", "quality_gp"} <= keys


def test_fundamental_factors_are_not_cheap_trials():
    # Deflated-Sharpe trials must be network-free (price/alpha only) — no fundamentals fetch.
    trials = set(fac.trial_keys())
    assert "value_bp" not in trials and "value" not in trials and "quality" not in trials
    assert "momentum" in trials  # a price factor is a trial


def test_every_fundamental_factor_has_a_metric_mapping():
    # Any value/quality factor must have a PIT metric, or build_signed KeyErrors. Composites build
    # via their components, so they need no direct metric mapping.
    for key, spec in fac.CATALOG.items():
        if spec["kind"] not in ("price", "alpha", "composite"):
            assert key in fac._FUNDAMENTAL_METRIC, key

    # Composites must reference real component factors instead.
    for key, spec in fac.CATALOG.items():
        if spec["kind"] == "composite":
            assert all(c in fac.CATALOG for c in spec["components"]), key
