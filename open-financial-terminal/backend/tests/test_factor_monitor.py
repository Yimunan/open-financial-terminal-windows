"""Tests for the factor performance monitoring service: scorecard, single-factor drill-down,
and persisted monitor sets + snapshot history.

Uses the real DataManager over the local dow30 lake (skips if uncached) and a temp TerminalStore
so monitors/snapshots never touch the real DB.

Run: `cd backend && pytest tests/test_factor_monitor.py -v`
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app import deps as appdeps
from app.services import factor_monitor as fm
from app.store import TerminalStore


@pytest.fixture(scope="module")
def providers():
    return (appdeps.get_data_manager(), appdeps.get_fundamentals_store(), appdeps.get_fundamentals_provider())


@pytest.fixture
def store(tmp_path):
    s = TerminalStore(tmp_path / "oft.sqlite")
    s.init()
    return s


def _call(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        if "insufficient data" in str(e):
            pytest.skip(f"dow30 not cached: {e}")
        raise


def test_scorecard_ranks_trial_factors(providers):
    dm, fs, fp = providers
    board = _call(lambda: fm.scorecard(dm, fs, fp, "dow30", None, horizon=5, q=5))
    assert board["n_instruments"] > 5 and board["rows"], board.get("errors")
    r0 = board["rows"][0]
    assert {"factor", "mean_ic", "ic_ir", "t_stat", "hit_rate", "q_spread", "autocorr", "n"} <= set(r0)
    assert r0["n"] > 0
    # sorted by ic_ir descending
    irs = [r["ic_ir"] for r in board["rows"]]
    assert irs == sorted(irs, reverse=True)


def test_factor_detail_has_all_series(providers):
    dm, fs, fp = providers
    d = _call(lambda: fm.factor_detail(dm, fs, fp, "dow30", "momentum", horizon=5, q=5))
    assert d["factor"] == "momentum"
    assert len(d["ic_series"]) > 0
    assert len(d["ic_decay"]) == 6
    assert len(d["quantile_returns"]) == 5
    assert "turnover_series" in d
    assert d["metrics"]["n"] > 0 and "ic_ir" in d["metrics"]
    # time-series shape
    assert set(d["ic_series"][0]) == {"time", "value"}


def test_monitor_crud(store):
    fm.save_monitor(store, "M1", {"universe": "dow30", "factors": ["momentum", "reversal"], "horizon": 5})
    mons = fm.list_monitors(store)["monitors"]
    assert len(mons) == 1 and mons[0]["name"] == "M1"
    assert mons[0]["factors"] == ["momentum", "reversal"]
    fm.remove_monitor(store, "M1")
    assert fm.list_monitors(store)["monitors"] == []


def test_run_monitor_writes_snapshots_and_history(providers, store):
    dm, fs, fp = providers
    fm.save_monitor(store, "Dow", {"universe": "dow30", "factors": ["momentum", "reversal"], "horizon": 5})
    r1 = _call(lambda: fm.run_monitor(dm, fs, fp, store, "Dow"))
    assert r1["snapshot_id"] == 1 and len(r1["rows"]) == 2
    fm.run_monitor(dm, fs, fp, store, "Dow")  # second snapshot

    hist = fm.monitor_history(store, "Dow")
    assert hist["n_snapshots"] == 2
    assert "momentum" in hist["factors"]
    assert len(hist["factors"]["momentum"]["mean_ic"]) == 2
    assert set(hist["factors"]["momentum"]["mean_ic"][0]) == {"time", "value"}


def test_run_unknown_monitor_raises(providers, store):
    dm, fs, fp = providers
    with pytest.raises(ValueError, match="unknown monitor"):
        fm.run_monitor(dm, fs, fp, store, "nope")


# ── 3-layer drill-down helpers (deterministic, synthetic panels — no data lake) ───────
def _synthetic(n: int = 10, days: int = 150):
    """Prices where instrument i has a constant daily return increasing with i, plus a score
    panel that ranks instruments by i (higher = long). A long-short decile book is then profitable."""
    idx = pd.date_range("2022-01-03", periods=days, freq="B")
    daily = np.linspace(-0.002, 0.002, n)                       # per-instrument constant daily return
    prices = pd.DataFrame(100.0 * np.cumprod(1 + np.tile(daily, (days, 1)), axis=0),
                          index=idx, columns=[f"S{i}" for i in range(n)])
    scores = pd.DataFrame(np.tile(np.arange(n, dtype=float), (days, 1)), index=idx, columns=prices.columns)
    return scores, prices


def test_ls_returns_sign_and_curve():
    scores, prices = _synthetic()
    ls = fm._ls_returns(scores, prices, top=0.1)
    assert len(ls) > 0
    assert ls.mean() > 0                                        # top decile beats bottom decile
    assert (1 + ls).cumprod().iloc[-1] > 1.0


def test_monotonicity():
    mono = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5], index=[1, 2, 3, 4, 5])
    assert fm._monotonicity(mono) == 1.0
    noisy = pd.Series([0.3, 0.1, 0.5, 0.2, 0.4], index=[1, 2, 3, 4, 5])
    assert fm._monotonicity(noisy) < 1.0
    assert fm._monotonicity(pd.Series([0.1], index=[1])) is None


def test_beta_recovers_known_slope():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2022-01-03", periods=250, freq="B")
    bench = pd.Series(rng.normal(0, 0.01, 250), index=idx)
    ls = 2.0 * bench + pd.Series(rng.normal(0, 0.0002, 250), index=idx)
    out = fm._beta(ls, bench)
    assert abs(out["beta"] - 2.0) < 0.1
    assert out["market_corr"] > 0.9
    # too few overlapping points → graceful None
    assert fm._beta(ls.iloc[:5], bench.iloc[:5])["beta"] is None


def test_regime_rows_shape_and_empty():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2022-01-03", periods=200, freq="B")
    bench = pd.Series(rng.normal(0, 0.01, 200), index=idx)
    ic = pd.Series(rng.normal(0, 0.05, 200), index=idx)
    ls = pd.Series(rng.normal(0, 0.01, 200), index=idx)
    rows = fm._regime_rows(ic, ls, bench)
    assert [r["regime"] for r in rows] == ["High vol", "Low vol", "Bull", "Bear"]
    assert all(set(r) == {"regime", "ic", "ls_return", "n"} for r in rows)
    assert fm._regime_rows(ic, ls, None) == []


# ── 3-layer drill-down end-to-end (real data; skips if uncached) ──────────────────────
def test_factor_detail_has_three_layers(providers):
    dm, fs, fp = providers
    d = _call(lambda: fm.factor_detail(dm, fs, fp, "dow30", "momentum", horizon=5, q=5))
    # Returns layer
    assert "returns" in d and d["returns"]["ls_curve"]
    assert set(d["returns"]) >= {"ls_curve", "ls_drawdown", "ls_total_return", "ls_sharpe", "quantile_monotonicity"}
    assert set(d["returns"]["ls_curve"][0]) == {"time", "value"}
    # Risk layer (beta may be None offline, but keys + a correlation list must exist)
    assert "risk" in d and isinstance(d["risk"]["factor_correlations"], list)
    assert set(d["risk"]) >= {"factor_correlations", "beta", "market_corr", "alpha_annual", "benchmark"}
    # Health layer
    assert "health" in d and isinstance(d["health"]["regimes"], list)


def test_correlation_matrix_is_square_symmetric_unit_diagonal(providers):
    dm, fs, fp = providers
    m = _call(lambda: fm.correlation_matrix(dm, fs, fp, "dow30"))
    n = len(m["factors"])
    assert n >= 2 and len(m["labels"]) == n
    assert len(m["matrix"]) == n and all(len(row) == n for row in m["matrix"])
    # unit diagonal and symmetry
    for i in range(n):
        assert abs(m["matrix"][i][i] - 1.0) < 1e-6
        for j in range(n):
            assert abs(m["matrix"][i][j] - m["matrix"][j][i]) < 1e-6


def test_correlation_matrix_needs_two_factors(providers):
    dm, fs, fp = providers
    with pytest.raises(ValueError, match="at least two factors"):
        _call(lambda: fm.correlation_matrix(dm, fs, fp, "dow30", factors=["momentum"]))


# ── factor-library wiring: custom factors ────────────────────────────────────────────
def test_default_keys_includes_custom_factors():
    class FakeStore:
        def list_custom_factors(self):
            return [{"name": "foo"}, {"name": "bar"}]

    keys = fm._default_keys(FakeStore())
    assert "foo" in keys and "bar" in keys
    assert "momentum" in keys  # built-in trial factors still present


def test_signed_scores_unknown_factor_raises():
    with pytest.raises(ValueError, match="unknown factor"):
        fm._signed_scores(None, None, None, None, None, pd.DataFrame(), "no_such_factor")


_CUSTOM = {"name": "my_mom", "kind": "alpha", "direction": "high=long", "code": "result = close.pct_change(20)"}


def test_custom_factor_in_scorecard_and_detail(providers, store):
    dm, fs, fp = providers
    store.save_custom_factor("my_mom", _CUSTOM)
    board = _call(lambda: fm.scorecard(dm, fs, fp, "dow30", None, horizon=5, q=5, store=store))
    assert any(r["factor"] == "my_mom" for r in board["rows"]), board.get("errors")

    d = _call(lambda: fm.factor_detail(dm, fs, fp, "dow30", "my_mom", horizon=5, q=5, store=store))
    assert d["factor"] == "my_mom"
    assert d["returns"]["ls_curve"] and "risk" in d and "health" in d


def test_custom_factor_signed_panel(providers, store):
    dm, fs, fp = providers
    store.save_custom_factor("my_mom", _CUSTOM)
    universe, prices = _call(lambda: fm._load_prices(dm, "dow30", 300))
    panel = fm._signed_scores(dm, fs, fp, store, universe, prices, "my_mom")
    assert not panel.empty and panel.shape[1] >= 3
