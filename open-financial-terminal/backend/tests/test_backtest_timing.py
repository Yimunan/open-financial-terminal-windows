"""Timed-rebalance + market-timing overlay helpers in backtest.py:
- `_weights_from_scores` rebalances less often as the cadence widens (monthly > quarterly > annual).
- `_timing_exposure` (trend) is risk-on above the benchmark SMA, scaled to `floor` below (lagged).
- `_timing_exposure` (regime) returns a clipped per-date exposure with regime diagnostics.
"""

import numpy as np
import pandas as pd
import pytest
from qhfi.core.types import AssetClass, Instrument, Universe

import app.services.backtest as bt


def _equity_universe() -> Universe:
    return Universe(
        name="t",
        instruments=[Instrument(id="AAA", asset_class=AssetClass.EQUITY),
                     Instrument(id="BBB", asset_class=AssetClass.EQUITY)],
    )


def test_rebalance_cadence_widens_with_freq():
    idx = pd.date_range("2020-01-01", "2021-12-31", freq="B", tz="UTC")
    months = idx.to_period("M")
    order = {mp: i for i, mp in enumerate(dict.fromkeys(months))}
    # scores rotate each month so the top-k selection can change every month-end
    data = {name: [float(np.sin(order[m] * 0.7 + j)) for m in months] for j, name in enumerate("ABCD")}
    scores = pd.DataFrame(data, index=idx)

    def rebals(freq: str) -> int:
        w = bt._weights_from_scores(scores, "long_only", 0.5, freq)
        return int((w.diff().abs().sum(axis=1) > 1e-9).sum())  # rows where weights changed

    n_m, n_q, n_y = rebals("M"), rebals("Q"), rebals("Y")
    assert n_m >= n_q >= n_y
    assert n_m > n_y  # cadence genuinely differs


def test_trend_exposure_risk_on_above_ma(monkeypatch):
    idx = pd.date_range("2020-01-01", periods=60, freq="D", tz="UTC")
    # benchmark rises 30 days then falls 30 → clearly crosses a 10-day SMA both ways
    close = pd.Series(np.concatenate([np.linspace(100, 130, 30), np.linspace(130, 100, 30)]), index=idx)
    monkeypatch.setattr("app.services.market.fetch_bars", lambda dm, s, a, d0, d1: (None, pd.DataFrame({"close": close})))
    prices = pd.DataFrame({"AAA": close, "BBB": close}, index=idx)

    exp, diag = bt._timing_exposure(None, _equity_universe(), prices, close.pct_change(), {"kind": "trend", "ma": 10, "floor": 0.0})
    assert diag["kind"] == "trend" and diag["params"]["ma"] == 10
    assert set(np.unique(exp.dropna().to_numpy())) <= {0.0, 1.0}
    assert exp.iloc[25] == 1.0   # deep in the rising leg → invested
    assert exp.iloc[-1] == 0.0   # deep in the falling leg → in cash

    # floor lifts the risk-off exposure
    exp2, _ = bt._timing_exposure(None, _equity_universe(), prices, close.pct_change(), {"kind": "trend", "ma": 10, "floor": 0.5})
    assert exp2.min() == 0.5


def test_no_benchmark_returns_none():
    uni = Universe(name="fx", instruments=[Instrument(id="EURUSD", asset_class=AssetClass.FX)])
    prices = pd.DataFrame({"EURUSD": [1.0, 1.1]}, index=pd.date_range("2020-01-01", periods=2, tz="UTC"))
    assert bt._timing_exposure(None, uni, prices, pd.Series([0.0, 0.1]), {"kind": "trend"}) is None


def test_regime_exposure_clipped_with_diagnostics(monkeypatch):
    idx = pd.date_range("2019-01-01", periods=320, freq="B", tz="UTC")
    rng = np.random.default_rng(0)
    # two volatility regimes (calm then turbulent) so the GMM has something to separate
    rets = np.concatenate([rng.normal(0.0005, 0.005, 160), rng.normal(-0.0005, 0.03, 160)])
    close = pd.Series(100 * np.cumprod(1 + rets), index=idx)
    monkeypatch.setattr("app.services.market.fetch_bars", lambda dm, s, a, d0, d1: (None, pd.DataFrame({"close": close})))
    prices = pd.DataFrame({"AAA": close, "BBB": close}, index=idx)

    out = bt._timing_exposure(None, _equity_universe(), prices, close.pct_change(), {"kind": "regime"})
    assert out is not None
    exp, diag = out
    assert diag["kind"] == "regime"
    assert len(diag["policy"]) == diag["params"]["n_regimes"]
    e = exp.dropna()
    assert (e >= 0.0).all() and (e <= 1.0).all()
