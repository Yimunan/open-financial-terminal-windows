"""Tests for the Chart Studio agent (services.chart_agent).

Hermetic: the LLM and every data source are stubbed, so the test exercises action validation,
the five tool resolvers, and the streamed frame sequence deterministically without the network
or the local model.

Run: `cd backend && pytest tests/test_chart_agent.py -q`
"""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from app.services import chart_agent as ca


def _bars(periods: int = 300, base: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=periods, freq="D", tz="UTC")
    close = base + np.arange(periods) * 0.2 + np.sin(np.arange(periods) / 4.0)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
         "volume": np.full(periods, 1e6)},
        index=idx,
    )


class _FakeRates:
    def load(self, _name):
        df = _bars()[["close"]].rename(columns={"close": "10Y"})
        df["2Y"] = df["10Y"] - 0.5
        return df


class _FakeMacro:
    def has(self, _sid):
        return True


class _FakeLLM:
    def __init__(self, action):
        self.action = action
        self.calls = 0

    def structured(self, system, user, schema, model=None):
        self.calls += 1
        return dict(self.action)


def _deps(action=None):
    return ca.ChartDeps(dm=None, macro_store=_FakeMacro(), rates_store=_FakeRates(),
                        llm=_FakeLLM(action or {}), model=None)


@pytest.fixture(autouse=True)
def _stub_sources(monkeypatch):
    monkeypatch.setattr(ca, "fetch_bars", lambda dm, sym, asset, *a, **k: (None, _bars()))
    monkeypatch.setattr(ca, "fetch_bars_intraday", lambda sym, asset, tf: (None, _bars()))
    monkeypatch.setattr(ca.mx, "rolling", lambda dm, sym, asset, window: {
        "window": window,
        "series": {"sharpe": [{"time": "2026-01-01", "value": 1.2}], "drawdown": [{"time": "2026-01-01", "value": -5.0}]},
    })
    monkeypatch.setattr(ca.pf, "risk", lambda dm, items, days=365: {
        "symbols": [it["symbol"] for it in items],
        "correlation": [[1.0, 0.5], [0.5, 1.0]],
        "metrics": [],
    })
    monkeypatch.setattr(ca.macro_svc, "series", lambda store, sid, s, e: {
        "label": "US CPI", "observations": [{"date": "2026-01-01", "value": 300.0}, {"date": "2026-02-01", "value": 301.0}],
    })
    # yfinance returns columns most-recent-first; the resolver must reverse to chronological.
    monkeypatch.setattr(ca.fa, "financials", lambda sym, n=5, freq="annual": {
        "periods": ["2025-12-31", "2024-12-31", "2023-12-31"],
        "rows": {
            "Total Revenue": [400.0, 300.0, 200.0],
            "Gross Profit": [200.0, 120.0, 80.0],
            "Net Income": [80.0, 60.0, 40.0],
            "Diluted EPS": [4.0, 3.0, 2.0],
        },
    })


def test_price_resolver_drops_bad_indicator_and_sets_open_params():
    out = ca.resolve_action(
        {"tool": "price", "title": "AAPL", "symbol": "aapl", "timeframe": "1d",
         "style": "candles", "indicators": ["sma:50", "rsi:14", "bogus:9"]},
        _deps(),
    )
    assert out["engine"] == "price"
    assert len(out["price"]["candles"]) > 0
    names = [i["name"] for i in out["price"]["indicators"]]
    assert names == ["sma:50", "rsi:14"]  # the unknown spec is dropped, not fatal
    assert out["open_params"]["chartType"] == "candles" and out["open_params"]["symbol"] == "AAPL"


def test_price_resolver_clamps_bad_timeframe():
    out = ca.resolve_action({"tool": "price", "title": "x", "symbol": "AAPL", "timeframe": "weekly"}, _deps())
    assert out["price"]["timeframe"] == "1d"


def test_compare_resolver_normalizes_each_symbol():
    out = ca.resolve_action(
        {"tool": "compare", "title": "cmp", "symbols": ["AAPL", "MSFT", "NVDA"], "lookback": "1Y"}, _deps())
    assert out["engine"] == "series"
    specs = out["series"]["specs"]
    assert [s["title"] for s in specs] == ["AAPL", "MSFT", "NVDA"]
    assert specs[0]["points"][0]["value"] == pytest.approx(0.0, abs=1e-6)  # normalized to 0 at start


def test_ratio_resolver_emits_ratio_and_mean():
    # both symbols resolve to the same stub bars → ratio is 1.0 throughout, mean 1.0.
    out = ca.resolve_action({"tool": "ratio", "title": "r", "symbols": ["GLD", "SLV"], "lookback": "1Y"}, _deps())
    assert out["engine"] == "series"
    specs = out["series"]["specs"]
    assert [s["title"] for s in specs] == ["GLD / SLV", "mean"]
    assert specs[0]["points"][0]["value"] == pytest.approx(1.0)
    assert len(specs[1]["points"]) == 2 and specs[1]["points"][0]["value"] == pytest.approx(1.0)


def test_ratio_resolver_needs_two_symbols():
    with pytest.raises(ValueError):
        ca.resolve_action({"tool": "ratio", "title": "r", "symbols": ["GLD"]}, _deps())


def test_rolling_corr_emits_bounded_line():
    # both symbols resolve to the same stub bars → identical returns → correlation 1.0.
    out = ca.resolve_action(
        {"tool": "rolling_corr", "title": "rc", "symbols": ["AAPL", "MSFT"], "window": 90}, _deps())
    assert out["engine"] == "series"
    specs = out["series"]["specs"]
    assert len(specs) == 1 and specs[0]["title"] == "AAPL↔MSFT 90d"
    vals = [p["value"] for p in specs[0]["points"]]
    assert vals and all(-1.0001 <= v <= 1.0001 for v in vals)
    assert vals[-1] == pytest.approx(1.0)


def test_rolling_corr_needs_two_symbols():
    with pytest.raises(ValueError):
        ca.resolve_action({"tool": "rolling_corr", "title": "rc", "symbols": ["AAPL"]}, _deps())


def test_rolling_beta_against_benchmark():
    # symbol and benchmark resolve to the same stub bars → beta ≡ 1.0.
    out = ca.resolve_action(
        {"tool": "rolling_beta", "title": "b", "symbol": "AAPL", "benchmark": "SPY", "window": 90}, _deps())
    assert out["engine"] == "series"
    specs = out["series"]["specs"]
    assert specs[0]["title"] == "AAPL β vs SPY (90d)"
    assert specs[0]["points"][-1]["value"] == pytest.approx(1.0)
    assert specs[1]["title"] == "β=1" and len(specs[1]["points"]) == 2


def test_rolling_beta_rejects_self_benchmark():
    with pytest.raises(ValueError):
        ca.resolve_action({"tool": "rolling_beta", "title": "b", "symbol": "AAPL", "benchmark": "AAPL"}, _deps())


def test_trailing_returns_bars_value_axis():
    out = ca.resolve_action({"tool": "trailing_returns", "title": "tr", "symbol": "AAPL"}, _deps())
    ser = out["series"]
    assert out["engine"] == "series" and ser["xMode"] == "value"
    spec = ser["specs"][0]
    assert spec["kind"] == "histogram"
    labels = [t["label"] for t in ser["xTicks"]]
    assert "1M" in labels and "1Y" in labels
    assert "3Y" not in labels  # 300-bar stub can't reach 756 trading days
    assert len(spec["points"]) == len(ser["xTicks"])
    assert all(p["value"] > 0 for p in spec["points"])  # monotonic-up stub → positive


def test_vol_cone_value_axis_bands_are_ordered():
    out = ca.resolve_action({"tool": "vol_cone", "title": "vc", "symbol": "AAPL", "lookback": "3Y"}, _deps())
    ser = out["series"]
    assert out["engine"] == "series"
    assert ser["xMode"] == "value" and ser["xUnit"] == "d"
    by = {s["title"]: s["points"] for s in ser["specs"]}
    assert "median" in by and "current" in by
    n = len(ser["xTicks"])
    assert all(len(s["points"]) == n for s in ser["specs"])  # one point per horizon
    # min ≤ median ≤ max at the first horizon
    assert by["min"][0]["value"] <= by["median"][0]["value"] <= by["max"][0]["value"]


def test_rolling_drawdown_is_area_down():
    out = ca.resolve_action({"tool": "rolling", "title": "dd", "symbol": "NVDA", "metric": "drawdown", "window": 180}, _deps())
    spec = out["series"]["specs"][0]
    assert spec["kind"] == "area" and spec["colorKey"] == "down"


def test_seasonality_rectangular_calendar():
    # stub bars span ~300 days → roughly 10 monthly returns across 2 calendar years.
    out = ca.resolve_action({"tool": "seasonality", "title": "s", "symbol": "AAPL"}, _deps())
    hm = out["heatmap"]
    assert out["engine"] == "heatmap"
    assert hm["cols"] == ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    assert hm["fmt"] == "pct" and hm["vmax"] >= 5.0
    assert hm["rows"][-1] == "Avg"  # per-month average row appended
    assert all(len(r) == 12 for r in hm["matrix"])  # every row is a full 12-month grid
    assert "monthly returns" in hm["title"]


def test_correlation_resolver_yields_heatmap():
    out = ca.resolve_action({"tool": "correlation", "title": "c", "symbols": ["AAPL", "MSFT"]}, _deps())
    assert out["engine"] == "heatmap"
    assert out["heatmap"]["labels"] == ["AAPL", "MSFT"]
    assert out["heatmap"]["matrix"][0][1] == 0.5


def test_macro_tenor_and_series():
    tenor = ca.resolve_action({"tool": "macro", "title": "10Y", "tenor": "10Y"}, _deps())
    assert tenor["engine"] == "series" and tenor["series"]["specs"][0]["points"]
    cpi = ca.resolve_action({"tool": "macro", "title": "CPI", "series_id": "CPIAUCSL"}, _deps())
    assert cpi["series"]["title"] == "US CPI"


def test_fundamentals_revenue_is_chronological_bars():
    out = ca.resolve_action({"tool": "fundamentals", "title": "rev", "symbol": "AAPL", "fund_metric": "revenue"}, _deps())
    assert out["engine"] == "series"
    spec = out["series"]["specs"][0]
    assert spec["kind"] == "histogram"
    # reversed to oldest→newest
    assert [p["time"] for p in spec["points"]] == ["2023-12-31", "2024-12-31", "2025-12-31"]
    assert [p["value"] for p in spec["points"]] == [200.0, 300.0, 400.0]


def test_fundamentals_margin_is_computed_line():
    out = ca.resolve_action({"tool": "fundamentals", "title": "gm", "symbol": "AAPL", "fund_metric": "gross_margin"}, _deps())
    spec = out["series"]["specs"][0]
    assert spec["kind"] == "line"
    # Gross Profit / Total Revenue * 100, oldest→newest: 80/200, 120/300, 200/400
    assert [p["value"] for p in spec["points"]] == [40.0, 40.0, 50.0]


def test_fundamentals_bad_metric_falls_back_to_revenue():
    out = ca.resolve_action({"tool": "fundamentals", "title": "x", "symbol": "AAPL", "fund_metric": "ebitda"}, _deps())
    assert out["series"]["specs"][0]["title"] == "Total Revenue"


def test_distribution_histogram_value_axis_and_markers():
    out = ca.resolve_action(
        {"tool": "distribution", "title": "d", "symbol": "AAPL", "lookback": "1Y", "bins": 20}, _deps())
    ser = out["series"]
    assert out["engine"] == "series"
    assert ser["xMode"] == "value" and ser["xUnit"] == "%"
    hist = ser["specs"][0]
    assert hist["kind"] == "histogram" and len(hist["points"]) == 20
    assert sum(p["value"] for p in hist["points"]) == 299  # 300 stub bars → 299 daily returns
    # mean + 5% VaR vertical markers (2 points each, span 0→ymax)
    assert [s["kind"] for s in ser["specs"][1:]] == ["line", "line"]
    assert all(len(s["points"]) == 2 for s in ser["specs"][1:])


def test_curve_snapshot_value_axis_and_comparison():
    # 800 days of a 4-tenor curve so the ~1y-ago comparison overlay is present.
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=800, freq="D", tz="UTC")
    base = np.linspace(3.0, 4.0, 800)
    df = pd.DataFrame(
        {"3M": base - 0.5, "5Y": base, "10Y": base + 0.3, "30Y": base + 0.6}, index=idx
    )

    class _Rates:
        def load(self, _name):
            return df

    deps = ca.ChartDeps(dm=None, macro_store=_FakeMacro(), rates_store=_Rates(), llm=_FakeLLM({}), model=None)
    out = ca.resolve_action({"tool": "curve", "title": "yc"}, deps)
    ser = out["series"]
    assert out["engine"] == "series"
    assert ser["xMode"] == "value" and ser["xUnit"] == "y"
    assert [t["label"] for t in ser["xTicks"]] == ["3M", "5Y", "10Y", "30Y"]
    # tenors map to years on the x-axis (3M → 0.25), sorted short→long
    assert ser["specs"][0]["points"][0]["time"] == pytest.approx(0.25)
    assert [p["time"] for p in ser["specs"][0]["points"]] == [0.25, 5.0, 10.0, 30.0]
    assert len(ser["specs"]) == 2  # latest + prior-date overlay


def test_curve_drops_comparison_when_history_too_short():
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=30, freq="D", tz="UTC")
    df = pd.DataFrame({"3M": np.linspace(3, 3.1, 30), "10Y": np.linspace(4, 4.1, 30)}, index=idx)

    class _Rates:
        def load(self, _name):
            return df

    deps = ca.ChartDeps(dm=None, macro_store=_FakeMacro(), rates_store=_Rates(), llm=_FakeLLM({}), model=None)
    out = ca.resolve_action({"tool": "curve", "title": "yc"}, deps)
    assert len(out["series"]["specs"]) == 1  # no ~1y-ago row in 30 days of data


def test_term_spread_difference_over_time():
    # _FakeRates has 10Y and 2Y = 10Y − 0.5, so the spread is a constant 0.5pp.
    out = ca.resolve_action({"tool": "term_spread", "title": "ts", "tenor": "10Y", "tenor2": "2Y"}, _deps())
    assert out["engine"] == "series"
    spec = out["series"]["specs"][0]
    assert spec["title"] == "10Y − 2Y" and spec["kind"] == "area"
    assert spec["points"] and all(p["value"] == pytest.approx(0.5) for p in spec["points"])


def test_term_spread_rejects_tenor_absent_from_lake():
    with pytest.raises(ValueError):  # 7Y is not in the _FakeRates curve
        ca.resolve_action({"tool": "term_spread", "title": "ts", "tenor": "10Y", "tenor2": "7Y"}, _deps())


def test_macro_compare_indexes_each_series():
    out = ca.resolve_action(
        {"tool": "macro_compare", "title": "m", "series_ids": ["CPIAUCSL", "FEDFUNDS"]}, _deps())
    assert out["engine"] == "series"
    specs = out["series"]["specs"]
    assert len(specs) == 2
    assert out["series"]["title"] == "Macro comparison · indexed to 100"
    assert specs[0]["points"][0]["value"] == pytest.approx(100.0)  # indexed to 100 at the start


def test_macro_compare_zscore_centers_series():
    out = ca.resolve_action(
        {"tool": "macro_compare", "title": "m", "series_ids": ["CPIAUCSL", "UNRATE"], "norm": "zscore"}, _deps())
    # two-point stub → z-scores are ±1 (population std); mean is 0
    vals = [p["value"] for p in out["series"]["specs"][0]["points"]]
    assert sum(vals) == pytest.approx(0.0, abs=1e-6)


def test_macro_compare_needs_two_series():
    with pytest.raises(ValueError):
        ca.resolve_action({"tool": "macro_compare", "title": "m", "series_ids": ["CPIAUCSL"]}, _deps())


def test_unknown_tool_clamps_to_price():
    deps = _deps({"tool": "teleport", "title": "x", "symbol": "AAPL"})
    action = ca._get_action(deps, "draw something", {})
    assert action["tool"] == "price"


def _collect(message, deps, context=None):
    async def run():
        return [f async for f in ca.arun_chart_studio_agent(message, context, deps)]
    return asyncio.run(run())


def test_agent_streams_thought_chart_done():
    deps = _deps({"tool": "price", "title": "AAPL daily", "symbol": "AAPL", "timeframe": "1d", "rationale": "Plotting AAPL."})
    frames = _collect("chart AAPL", deps)
    kinds = [f["type"] for f in frames]
    assert kinds == ["thought", "chart", "done"]
    assert frames[1]["engine"] == "price" and frames[1]["title"] == "AAPL daily"


def test_refinement_passes_prior_action_into_prompt():
    deps = _deps({"tool": "price", "title": "AAPL", "symbol": "AAPL", "indicators": ["rsi:14"]})
    prior = {"tool": "price", "args": {"symbol": "AAPL"}}
    # the fake LLM ignores the prompt, but we assert the call happens with the refinement context
    frames = _collect("add RSI", deps, context={"action": prior})
    assert [f["type"] for f in frames] == ["thought", "chart", "done"]
    assert deps.llm.calls == 1
