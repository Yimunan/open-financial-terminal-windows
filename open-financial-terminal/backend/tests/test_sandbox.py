"""Tests for the code sandbox: run factor/strategy/portfolio in sandboxed + trusted modes, and
save to the right home (custom registry / drop-in dir / Portfolios).

Sandboxed-portfolio, save, AST-rejection and the trusted-dropin guard are hermetic (temp store,
real DataManager only for price lookups which degrade gracefully). The factor/strategy *runs*
use the local dow30 lake and skip if it isn't cached.

Run: `cd backend && pytest tests/test_sandbox.py -v`
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import deps as appdeps
from app.services import portfolio_book as pb
from app.services import registry as reg
from app.services import sandbox as sb
from app.store import TerminalStore


@pytest.fixture(scope="module")
def dm():
    return appdeps.get_data_manager()


@pytest.fixture
def deps(dm, tmp_path):
    s = TerminalStore(tmp_path / "oft.sqlite")
    s.init()
    return SimpleNamespace(dm=dm, store=s)


def _skip_no_data(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        if "insufficient data" in str(e) or "no holdings" in str(e):
            pytest.skip(f"dow30 not cached: {e}")
        raise


# ── templates ───────────────────────────────────────────────────────────────────
def test_templates_cover_all_modes():
    t = sb.templates()
    assert set(t["modes"]) == {"factor", "strategy", "portfolio"}
    assert set(t["trusts"]) == {"sandboxed", "trusted"}
    for m in t["modes"]:
        assert t["starters"][m]["sandboxed"] and t["starters"][m]["trusted"]


# ── run: factor ───────────────────────────────────────────────────────────────────
def test_factor_sandboxed_ranks_universe(deps):
    out = _skip_no_data(lambda: sb.run(
        deps, mode="factor", trust="sandboxed",
        code="result = close / close.shift(60) - 1", context={"universe": "dow30"},
    ))
    assert out["kind"] == "factor" and out["ok"]
    assert out["n_scored"] > 5 and len(out["ranking"]) == out["n_scored"]
    assert "symbol" in out["ranking"][0] and "score" in out["ranking"][0]


def test_factor_trusted_runs_subclass(deps):
    code = (
        "from qhfi.factors.base import Factor\n"
        "class F(Factor):\n"
        "    name='f_t'\n"
        "    direction=1\n"
        "    def compute(self, prices, universe):\n"
        "        return prices.pct_change(60)\n"
    )
    out = _skip_no_data(lambda: sb.run(deps, mode="factor", trust="trusted", code=code, context={"universe": "dow30"}))
    assert out["trust"] == "trusted" and out["name"] == "f_t" and out["ranking"]


def test_factor_sandboxed_rejects_imports(deps):
    with pytest.raises(ValueError, match="imports are not allowed"):
        sb.run(deps, mode="factor", trust="sandboxed", code="import os\nresult=close", context={"universe": "dow30"})


# ── run: strategy ─────────────────────────────────────────────────────────────────
def test_strategy_sandboxed_lab(deps):
    code = "fast=close.rolling(10).mean()\nslow=close.rolling(30).mean()\nresult=np.where(fast>slow,1,-1)"
    out = _skip_no_data(lambda: sb.run(deps, mode="strategy", trust="sandboxed", code=code, context={"symbol": "AAPL", "years": 3}))
    assert out["engine"] == "lab" and "stats" in out["preview"]


def test_strategy_trusted_portfolio_engine(deps):
    code = (
        "from qhfi.strategy.base import Strategy\n"
        "class S(Strategy):\n"
        "    name='s_t'\n"
        "    def generate_weights(self, prices, universe):\n"
        "        a = prices.notna().astype(float)\n"
        "        return a.div(a.sum(axis=1), axis=0).fillna(0.0)\n"
    )
    out = _skip_no_data(lambda: sb.run(deps, mode="strategy", trust="trusted", code=code, context={"universe": "dow30", "years": 2}))
    assert out["engine"] == "portfolio" and out["preview"]["metrics"]["sharpe"] is not None


def test_trusted_strategy_without_subclass_errors(deps):
    with pytest.raises(ValueError, match="no qhfi Strategy subclass"):
        sb.run(deps, mode="strategy", trust="trusted", code="x = 1", context={"universe": "dow30"})


# ── run: portfolio (no market data required) ──────────────────────────────────────
def test_portfolio_sandboxed_normalizes(deps):
    out = sb.run(
        deps, mode="portfolio", trust="sandboxed",
        code="result = {'AAPL': 2, 'MSFT': 1, 'IBM': -1}", context={"mode": "long_short", "capital": 1_000_000},
    )
    assert out["kind"] == "portfolio"
    assert out["exposures"]["gross"] == pytest.approx(1.0, abs=1e-6)
    assert out["exposures"]["net"] == pytest.approx(0.0, abs=1e-6)
    assert "rows" in out["allocation"]


def test_portfolio_empty_result_errors(deps):
    with pytest.raises(ValueError, match="no weights"):
        sb.run(deps, mode="portfolio", trust="sandboxed", code="result = {}", context={})


# ── save ──────────────────────────────────────────────────────────────────────────
def test_save_custom_factor_and_strategy(deps):
    sb.save(deps, mode="factor", trust="sandboxed", name="sb_factor", code="result = close", meta={})
    sb.save(deps, mode="strategy", trust="sandboxed", name="sb_strat", code="result = np.ones(len(close))", meta={})
    assert any(f["name"] == "sb_factor" for f in deps.store.list_custom_factors())
    assert any(s["name"] == "sb_strat" for s in deps.store.list_custom_strategies())


def test_save_portfolio_from_run(deps):
    run = sb.run(deps, mode="portfolio", trust="sandboxed", code="result={'AAPL':0.6,'MSFT':0.4}", context={"mode": "long_only"})
    sb.save(deps, mode="portfolio", trust="sandboxed", name="sb_book", code="", meta={"mode": "long_only"}, allocations=run["allocations"])
    assert any(p["name"] == "sb_book" for p in pb.list_portfolios(deps.store)["portfolios"])


def test_trusted_dropin_guarded_against_qhfi_package(deps):
    # default factors_dir points at the qhfi package — saving a trusted drop-in there is refused
    with pytest.raises(ValueError, match="set a custom"):
        sb.save(deps, mode="factor", trust="trusted", name="x", code="x=1", meta={})


def test_trusted_dropin_writes_and_registers(deps, tmp_path):
    fdir = tmp_path / "myfactors"
    deps.store.set_config("registry.factors_dir", str(fdir))
    code = (
        "from qhfi.factors.base import Factor\n"
        "from qhfi.factors.registry import register\n"
        "@register\n"
        "class Dropped(Factor):\n"
        "    name='sb_dropped'\n"
        "    direction=1\n"
        "    def compute(self, prices, universe):\n"
        "        return prices.pct_change()\n"
    )
    out = sb.save(deps, mode="factor", trust="trusted", name="sb dropped", code=code, meta={})
    assert out["saved"] == "dropin_factor"
    assert (fdir / "sb_dropped.py").exists()
    # it registered into the live qhfi factor registry (surfaced via engine_factors)
    assert "sb_dropped" in [f["name"] for f in reg.engine_factors(deps.store)]
