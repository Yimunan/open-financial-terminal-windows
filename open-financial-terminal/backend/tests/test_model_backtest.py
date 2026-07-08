"""Integration test for the direct model→backtest path (`engine_strategy.run_model_backtest`).

Saves a real model bundle into the store, back-tests it (over the cached dow30 lake), and checks
the bundle's config — including stored params (top_pct / years) — is reproduced. Cleans up the
model afterwards so the real store is left untouched.
"""

from __future__ import annotations

import pytest

from app import deps as appdeps
from app.services import engine_strategy as eng
from app.services import registry as reg


@pytest.fixture()
def store_and_providers():
    store = appdeps.get_store()
    dm = appdeps.get_data_manager()
    fstore = appdeps.get_fundamentals_store()
    fprov = appdeps.get_fundamentals_provider()
    return store, dm, fstore, fprov


def test_unknown_model_raises(store_and_providers):
    store, dm, fstore, fprov = store_and_providers
    with pytest.raises(ValueError, match="unknown model"):
        eng.run_model_backtest(dm, store, fstore, fprov, "__no_such_model__")


def test_factor_model_reproduces_config(store_and_providers):
    store, dm, fstore, fprov = store_and_providers
    name = "__test_mom_model__"
    reg.save_model(store, name, {
        "factor": "momentum", "universe": "dow30", "mode": "long_short",
        "params": {"top_pct": 0.3, "years": 2},
    })
    try:
        out = eng.run_model_backtest(dm, store, fstore, fprov, name)
        assert out["model"] == name
        assert out["factor"] == "momentum"
        assert out["mode"] == "long_short"
        assert out["universe"] == "dow30"
        # honored the stored 2-year window (≈ 2 years of daily points, not the 3y default)
        assert out["metrics"]["sharpe"] is not None
        # explicit override beats the stored param
        out3 = eng.run_model_backtest(dm, store, fstore, fprov, name, years=1)
        assert out3["window_start"] > out["window_start"]  # 1y window starts later than 2y
    finally:
        reg.remove_model(store, name)


def test_empty_model_bundle_raises(store_and_providers):
    store, dm, fstore, fprov = store_and_providers
    name = "__test_empty_model__"
    reg.save_model(store, name, {"universe": "dow30"})  # no factor, no strategy
    try:
        with pytest.raises(ValueError, match="neither a factor nor a strategy"):
            eng.run_model_backtest(dm, store, fstore, fprov, name)
    finally:
        reg.remove_model(store, name)
