"""Tests for the qhfi-engine links: drop-in module loading, the strategy engine run, and the
trained-model repository.

The drop-in + repository tests are hermetic (temp dirs). The strategy-engine run uses the real
``BacktestEngine`` over the local dow30 lake (like ``test_backtest_agent``) and is skipped if that
data isn't cached.

Run: `cd backend && pytest tests/test_engine_modules.py -v`
"""

from __future__ import annotations

import textwrap

import pytest

from app import deps as appdeps
from app.services import engine_strategy as eng
from app.services import model_repo as mrepo
from app.services import registry as reg


class FakeStore:
    """Minimal store: registry path config points every linked dir at one scratch folder."""

    def __init__(self, factors="", strategies="", models=""):
        self._cfg = {
            "registry.factors_dir": factors,
            "registry.strategies_dir": strategies,
            "registry.models_dir": models,
        }

    def get_config(self, key, default=None):
        return self._cfg.get(key) or default

    def list_custom_factors(self):
        return []

    def list_custom_strategies(self):
        return []


_DROPIN_FACTOR = textwrap.dedent('''
    """A drop-in factor that should register into qhfi's factor registry."""
    from qhfi.factors.base import Factor
    from qhfi.factors.registry import register

    @register
    class DropinTestFactor(Factor):
        name = "dropin_test_factor"
        direction = -1
        def compute(self, prices, universe):
            return prices.pct_change()
''')

_DROPIN_STRATEGY = textwrap.dedent('''
    """A drop-in long-only equal-weight strategy (fully implemented, so it runs)."""
    from qhfi.strategy.base import Strategy
    from qhfi.strategy.registry import register

    @register
    class DropinEqualWeight(Strategy):
        name = "dropin_eqw"
        def generate_weights(self, prices, universe):
            avail = prices.notna().astype(float)
            return avail.div(avail.sum(axis=1), axis=0).fillna(0.0)
''')


def test_load_dir_registers_dropin_factor(tmp_path):
    (tmp_path / "my_factor.py").write_text(_DROPIN_FACTOR, encoding="utf-8")
    store = FakeStore(factors=str(tmp_path))

    out = reg.load_dir_modules(str(tmp_path))
    assert "my_factor" in out["loaded"], out
    assert out["errors"] == []

    names = [f["name"] for f in reg.engine_factors(store)]
    assert "dropin_test_factor" in names
    linked = next(f for f in reg.engine_factors(store) if f["name"] == "dropin_test_factor")
    assert linked["source"] == "linked"
    assert linked["direction"] == "low=long"  # direction = -1


def test_load_dir_reports_bad_file(tmp_path):
    (tmp_path / "broken.py").write_text("this is not valid python :::", encoding="utf-8")
    out = reg.load_dir_modules(str(tmp_path))
    assert out["loaded"] == []
    assert out["errors"] and out["errors"][0]["file"] == "broken.py"


def test_internal_qhfi_dir_is_not_re_executed():
    # The default factors_dir is inside the qhfi package; loading it must not raise
    # duplicate-registration errors — it's skipped (already imported at startup).
    out = reg.load_dir_modules(reg.default_paths()["factors_dir"])
    assert out["errors"] == []


def test_model_repo_list_and_promote(tmp_path):
    from qhfi.models import ModelRepository

    repo = ModelRepository(root=str(tmp_path))
    repo.save("demo", {"w": 1}, metrics={"ic": 0.03}, framework="sklearn")
    repo.save("demo", {"w": 2}, metrics={"ic": 0.05})
    store = FakeStore(models=str(tmp_path))

    listing = mrepo.list_repo_models(store)
    assert listing["exists"] is True
    model = listing["models"][0]
    assert model["name"] == "demo"
    assert model["latest"] == 2
    assert model["production_version"] is None
    assert len(model["versions"]) == 2

    promoted = mrepo.promote_model(store, "demo", 2, "production")
    assert promoted["stage"] == "production"
    assert mrepo.list_repo_models(store)["models"][0]["production_version"] == 2

    with pytest.raises(ValueError):
        mrepo.promote_model(store, "demo", 1, "bogus_stage")


def test_engine_strategy_listing_includes_builtins():
    store = FakeStore(strategies=reg.default_paths()["strategies_dir"])
    names = [s["name"] for s in eng.list_engine_strategies(store)]
    assert {"momentum", "model", "mdp"} <= set(names)


def test_engine_strategy_stub_surfaces_clean_error():
    store = FakeStore(strategies=reg.default_paths()["strategies_dir"])
    dm = appdeps.get_data_manager()
    try:
        with pytest.raises(ValueError) as ei:
            eng.run_engine_strategy(dm, store, strategy_key="momentum", universe_name="dow30", years=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"dow30 data not available: {e}")
    assert "not yet implemented" in str(ei.value)


def test_engine_strategy_dropin_runs_full_dashboard(tmp_path):
    (tmp_path / "eqw.py").write_text(_DROPIN_STRATEGY, encoding="utf-8")
    store = FakeStore(strategies=str(tmp_path))
    dm = appdeps.get_data_manager()
    try:
        out = eng.run_engine_strategy(dm, store, strategy_key="dropin_eqw", universe_name="dow30", years=3)
    except ValueError as e:
        if "insufficient data" in str(e):
            pytest.skip("dow30 data not cached")
        raise
    assert out["strategy"] == "dropin_eqw"
    assert {"metrics", "equity_curve", "drawdown_curve", "robustness", "top_weights"} <= set(out.keys())
    assert out["metrics"]["sharpe"] is not None
