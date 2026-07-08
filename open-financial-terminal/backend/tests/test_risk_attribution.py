"""Unit tests for the risk-attribution service's pure decomposition (`_attribute`).

Offline + seeded: fit a real qhfi BarraRiskModel on synthetic panels (no lake / network), then
check the response shape and the Euler identities the UI relies on — per-position contributions
sum to the total vol, factor contributions sum to the factor variance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.risk_attribution import _attribute, _attribute_brinson, _attribute_returns
from qhfi.barra.model import BarraRiskModel
from qhfi.core.types import AssetClass, EquityMeta, Instrument, Universe
from qhfi.factors.market import MarketPanels


def _model() -> BarraRiskModel:
    rng = np.random.default_rng(7)
    n, t = 12, 600
    dates = pd.date_range("2019-01-01", periods=t, freq="B", tz="UTC")
    ids = [f"A{i}" for i in range(n)]
    sectors = [["Tech", "Health", "Financials"][i % 3] for i in range(n)]
    rets = rng.normal(0.0004, 0.015, (t, n))
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=dates, columns=ids)
    vol = pd.DataFrame(rng.uniform(1e6, 5e6, (t, n)), index=dates, columns=ids)
    panels = MarketPanels(open=close, high=close, low=close, close=close, volume=vol)
    universe = Universe(name="_t", instruments=[
        Instrument(id=i, asset_class=AssetClass.EQUITY, exchange="x",
                   equity=EquityMeta(gics_sector=s)) for i, s in zip(ids, sectors, strict=True)
    ])
    return BarraRiskModel.from_panels(panels, universe)


def _payload() -> dict:
    model = _model()
    names = list(model.exposures_.index)
    w = pd.Series(1.0 / len(names), index=names)
    return _attribute(model, w, source="holdings", as_of="2026-06-23", skipped=[],
                      base_universe="_t", window_days=504)


def test_response_shape():
    p = _payload()
    assert p["source"] == "holdings" and p["n"] > 0
    assert {"total_vol", "factor_vol", "specific_vol", "pct_factor", "factors", "positions"} <= p.keys()
    assert 0.0 <= p["pct_factor"] <= 1.0
    assert {f["kind"] for f in p["factors"]} <= {"style", "industry"}


def test_position_contributions_sum_to_total_vol():
    p = _payload()
    # cctr/total_vol are percent (×100); pct is a fraction summing to 1 (Euler)
    # abs tolerance absorbs the 2-dp rounding of each per-name cctr; the raw Euler identity is exact
    assert sum(pos["cctr"] for pos in p["positions"]) == pytest.approx(p["total_vol"], abs=0.1)
    assert sum(pos["pct"] for pos in p["positions"]) == pytest.approx(1.0, abs=1e-3)


def test_factor_contributions_match_factor_variance():
    p = _payload()
    # per-factor variance contributions sum to the aggregate factor variance ((factor_vol/100)²)
    fac_var = sum(f["var_contribution"] for f in p["factors"])
    assert fac_var == pytest.approx((p["factor_vol"] / 100.0) ** 2, rel=1e-3, abs=1e-6)
    assert sum(f["pct_total"] for f in p["factors"]) == pytest.approx(p["pct_factor"], rel=1e-3, abs=1e-6)


def _return_payload() -> dict:
    model = _model()
    names = list(model.exposures_.index)
    w = pd.Series(1.0 / len(names), index=names)
    return _attribute_returns(model, w, source="holdings", as_of="2026-06-23", skipped=[],
                              base_universe="_t", window_days=504)


def test_return_attribution_shape_and_identity():
    p = _return_payload()
    assert p["n"] > 0
    assert {"total_return", "factor_return", "specific_return", "contributions", "series"} <= p.keys()
    # factor + specific must equal the realized total (the decomposition is exact, modulo 2-dp)
    assert p["factor_return"] + p["specific_return"] == pytest.approx(p["total_return"], abs=0.1)
    # per-factor contributions sum to the factor return
    assert sum(c["contribution"] for c in p["contributions"]) == pytest.approx(p["factor_return"], abs=0.1)
    # cumulative series is aligned and non-empty
    s = p["series"]
    assert len(s["times"]) == len(s["total"]) == len(s["factor"]) == len(s["specific"]) > 0


def _brinson_payload() -> dict:
    model = _model()
    names = list(model.exposures_.index)
    # hold a 3-name subset so port ≠ benchmark (active return is nonzero)
    w = pd.Series(1.0 / 3, index=names[:3])
    return _attribute_brinson(model, w, source="holdings", as_of="2026-06-23", skipped=[],
                              base_universe="_t", window_days=504)


def test_brinson_attribution_identity():
    p = _brinson_payload()
    assert {"active_return", "allocation", "selection", "interaction", "sectors"} <= p.keys()
    # allocation + selection + interaction = active return (Brinson–Fachler identity)
    assert p["allocation"] + p["selection"] + p["interaction"] == pytest.approx(p["active_return"], abs=0.1)
    # each sector row: alloc + sel + interaction = its total; sector totals sum to the active return
    for s in p["sectors"]:
        assert s["allocation"] + s["selection"] + s["interaction"] == pytest.approx(s["total"], abs=0.05)
    assert sum(s["total"] for s in p["sectors"]) == pytest.approx(p["active_return"], abs=0.1)
