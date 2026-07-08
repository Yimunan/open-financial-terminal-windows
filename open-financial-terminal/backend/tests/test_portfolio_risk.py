"""Tests for portfolio-LEVEL risk (services.portfolio.portfolio_risk).

Prices the book off the local lake and is skipped if nothing could be priced (offline / no
cached data). Asserts the aggregate shape and the gross-normalisation invariant.

Run: `cd backend && pytest tests/test_portfolio_risk.py -v`
"""

from __future__ import annotations

import pytest

from app import deps as appdeps
from app.services import portfolio as pf


def test_portfolio_risk_long_only_aggregate():
    dm = appdeps.get_data_manager()
    out = pf.portfolio_risk(
        dm,
        [
            {"symbol": "AAPL", "asset": "equity", "quantity": 10},
            {"symbol": "MSFT", "asset": "equity", "quantity": 5},
        ],
        days=365,
    )
    if out.get("insufficient"):
        pytest.skip("no cached prices / history for AAPL/MSFT")

    assert out["n"] == 2
    # long-only book: gross == 1 (normalised) and net == 1 (no shorts)
    assert out["gross"] == pytest.approx(1.0, abs=1e-6)
    assert out["net"] == pytest.approx(1.0, abs=1e-6)
    assert out["n_long"] == 2 and out["n_short"] == 0
    # core fields present and finite
    for k in ("ann_vol", "sharpe", "sortino", "max_drawdown", "var_95", "cvar_95", "concentration"):
        assert isinstance(out[k], (int, float))
    assert out["var_95"] >= 0  # 95% loss quantile expressed as a positive %
    assert out["cvar_95"] >= out["var_95"]  # tail mean is at least the threshold
    assert 0 < out["concentration"] <= 1
    assert out["benchmark"] == "SPY"
