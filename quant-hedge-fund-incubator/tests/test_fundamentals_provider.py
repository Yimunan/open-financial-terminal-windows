"""Offline tests for the yfinance fundamentals provider (PIT reporting-lag, metric math) and
the FundamentalsStore round-trip/panel. yfinance is monkeypatched — no network."""

from __future__ import annotations

import pandas as pd
import pytest
import yfinance

from qhfi.core.types import AssetClass, DateRange, Instrument
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider


def _fake_ticker():
    # 4 quarters, period-end columns (most recent first, as yfinance returns)
    cols = pd.to_datetime(["2025-03-31", "2024-12-31", "2024-09-30", "2024-06-30"])
    income = pd.DataFrame(
        {c: v for c, v in zip(cols, [
            [100, 40, 10],   # 2025-03-31: revenue, gross profit, net income
            [90, 36, 9], [80, 32, 8], [70, 28, 7],
        ])},
        index=["Total Revenue", "Gross Profit", "Net Income"],
    )
    balance = pd.DataFrame(
        {c: v for c, v in zip(cols, [[200, 50], [195, 50], [190, 50], [185, 50]])},
        index=["Stockholders Equity", "Total Debt"],
    )

    class T:
        quarterly_income_stmt = income
        quarterly_balance_sheet = balance

    return T()


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(yfinance, "Ticker", lambda _id: _fake_ticker())
    return YFinanceFundamentalsProvider(reporting_lag_days=60)


SPAN = DateRange(start=pd.Timestamp("2024-01-01").date(), end=pd.Timestamp("2026-06-04").date())
AAPL = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)


def test_reporting_lag_makes_it_point_in_time(provider):
    gm = provider.fetch(AAPL, "gross_margin", SPAN)
    # latest quarter ends 2025-03-31; knowable date = +60d = 2025-05-30
    assert gm.index.max() == pd.Timestamp("2025-05-30", tz="UTC")
    assert gm.iloc[-1] == pytest.approx(0.40)            # 40/100


def test_roe_is_ttm_over_equity(provider):
    roe = provider.fetch(AAPL, "roe", SPAN)
    # TTM net income for the latest = 10+9+8+7 = 34; equity 200 → 0.17
    assert roe.iloc[-1] == pytest.approx(34 / 200)


def test_debt_to_equity(provider):
    de = provider.fetch(AAPL, "debt_to_equity", SPAN)
    assert de.iloc[-1] == pytest.approx(50 / 200)


def test_store_roundtrip_and_panel(tmp_path, provider):
    store = FundamentalsStore(tmp_path)
    b = Instrument(id="MSFT", asset_class=AssetClass.EQUITY)
    store.save(AAPL, "roe", provider.fetch(AAPL, "roe", SPAN))
    store.save(b, "roe", provider.fetch(b, "roe", SPAN))

    assert store.has(AAPL, "roe")
    assert store._path(AAPL, "roe").parts[-3:] == ("fundamental", "roe", "AAPL.parquet")
    panel = store.panel([AAPL, b], "roe")
    assert list(panel.columns) == ["AAPL", "MSFT"]
    assert str(panel.index.tz) == "UTC"
