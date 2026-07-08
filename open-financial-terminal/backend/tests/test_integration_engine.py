"""End-to-end integration test for the qhfi-engine links.

Drives the real FastAPI app through a TestClient — router + deps + real store + the qhfi
registries / BacktestEngine / ModelRepository all run together. The flow mirrors what a user
does in the UI:

  1. Point the linked directories (Settings) at scratch folders holding a drop-in factor + a
     drop-in strategy, and a fresh ModelRepository.
  2. The Factor and Strategy widgets' "qhfi engine" lists now include the drop-ins.
  3. Run the drop-in strategy through the portfolio engine → full dashboard payload.
  4. The Model-Repository "Trained models" tab lists the repo's cards and can promote a stage.

The original linked paths are saved and restored, so the test leaves the real config untouched.
The strategy-run assertion is skipped if the local dow30 lake isn't cached.

Run: `cd backend && pytest tests/test_integration_engine.py -v`
"""

from __future__ import annotations

import textwrap

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Unique names so this test is independent of test_engine_modules' drop-ins in the same session.
_FACTOR = textwrap.dedent('''
    from qhfi.factors.base import Factor
    from qhfi.factors.registry import register

    @register
    class ITestMomentum(Factor):
        name = "itest_momentum"
        direction = 1
        def compute(self, prices, universe):
            return prices.pct_change(20)
''')

_STRATEGY = textwrap.dedent('''
    from qhfi.strategy.base import Strategy
    from qhfi.strategy.registry import register

    @register
    class ITestEqualWeight(Strategy):
        name = "itest_eqw"
        def generate_weights(self, prices, universe):
            avail = prices.notna().astype(float)
            return avail.div(avail.sum(axis=1), axis=0).fillna(0.0)
''')


@pytest.fixture
def client():
    return TestClient(app)


def test_full_engine_link_flow(client, tmp_path):
    factors_dir = tmp_path / "factors"
    strategies_dir = tmp_path / "strategies"
    models_dir = tmp_path / "models"
    for d in (factors_dir, strategies_dir, models_dir):
        d.mkdir()
    (factors_dir / "itest_factor.py").write_text(_FACTOR, encoding="utf-8")
    (strategies_dir / "itest_strategy.py").write_text(_STRATEGY, encoding="utf-8")

    # Seed a trained-model repository the way qhfi would.
    from qhfi.models import ModelRepository

    repo = ModelRepository(root=str(models_dir))
    repo.save("itest_model", {"w": 1}, metrics={"ic": 0.02}, framework="sklearn")
    repo.save("itest_model", {"w": 2}, metrics={"ic": 0.04})

    original = client.get("/api/registry/paths").json()
    try:
        # 1) Point the linked dirs at the scratch folders (the Settings "Save").
        saved = client.put(
            "/api/registry/paths",
            json={
                "factors_dir": str(factors_dir),
                "strategies_dir": str(strategies_dir),
                "models_dir": str(models_dir),
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["models_dir"] == str(models_dir)

        # 2) The drop-ins surface in the engine lists (Factor + Strategy widgets).
        factors = client.get("/api/registry/factors").json()
        assert "itest_momentum" in [f["name"] for f in factors["engine"]]
        linked_factor = next(f for f in factors["engine"] if f["name"] == "itest_momentum")
        assert linked_factor["source"] == "linked"

        strategies = client.get("/api/registry/strategies").json()
        assert "itest_eqw" in [s["name"] for s in strategies["engine"]]

        # 3) Run the drop-in strategy through the real portfolio engine → dashboard payload.
        run = client.post(
            "/api/registry/strategies/run",
            json={"strategy_key": "itest_eqw", "universe_name": "dow30", "years": 3, "mode": "long_only"},
        )
        if run.status_code == 400 and "insufficient data" in run.json().get("detail", ""):
            pytest.skip("dow30 data not cached")
        assert run.status_code == 200, run.text
        out = run.json()
        assert out["strategy"] == "itest_eqw"
        assert out["universe"] == "dow30"
        assert {"metrics", "equity_curve", "drawdown_curve", "top_weights"} <= set(out)
        assert out["metrics"]["sharpe"] is not None

        # 4) Trained-models tab: list + promote.
        repo_models = client.get("/api/registry/repo-models").json()
        assert repo_models["exists"] is True
        model = next(m for m in repo_models["models"] if m["name"] == "itest_model")
        assert model["latest"] == 2 and model["production_version"] is None

        promoted = client.post("/api/registry/repo-models/itest_model/promote", json={"version": 2, "stage": "production"})
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["stage"] == "production"
        after = client.get("/api/registry/repo-models").json()
        assert next(m for m in after["models"] if m["name"] == "itest_model")["production_version"] == 2
    finally:
        # Restore the user's real linked paths.
        client.put("/api/registry/paths", json=original)


def test_unknown_strategy_run_is_400(client):
    r = client.post("/api/registry/strategies/run", json={"strategy_key": "does_not_exist", "universe_name": "dow30"})
    assert r.status_code == 400
    assert "unknown strategy" in r.json()["detail"]
