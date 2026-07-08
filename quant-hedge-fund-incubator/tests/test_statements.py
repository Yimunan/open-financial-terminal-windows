"""Offline tests for the statements provider (yfinance monkeypatched) + StatementsStore."""

from __future__ import annotations

import pandas as pd
import yfinance

from qhfi.core.types import AssetClass, Instrument
from qhfi.data.providers.statements_yfinance import YFinanceStatementsProvider
from qhfi.data.statements import StatementsStore

AAPL = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)


def _fake_ticker():
    cols = pd.to_datetime(["2025-03-31", "2024-12-31"])
    income = pd.DataFrame(
        {cols[0]: [100, 40], cols[1]: [90, 36]},
        index=["Total Revenue", "Net Income"],
    )

    class T:
        quarterly_income_stmt = income
        income_stmt = income

    return T()


def test_provider_transposes_to_periods_by_lineitems(monkeypatch):
    monkeypatch.setattr(yfinance, "Ticker", lambda _id: _fake_ticker())
    df = YFinanceStatementsProvider().fetch(AAPL, "income", "quarterly")
    assert list(df.columns) == ["Total Revenue", "Net Income"]      # line items are columns
    assert str(df.index.dtype).startswith("datetime")               # period-end dates are the index
    assert df.loc[pd.Timestamp("2025-03-31"), "Total Revenue"] == 100


def test_store_roundtrip_and_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(yfinance, "Ticker", lambda _id: _fake_ticker())
    store = StatementsStore(tmp_path)
    prov = YFinanceStatementsProvider()
    store.save(AAPL, "income", "quarterly", prov.fetch(AAPL, "income", "quarterly"))

    assert store.has(AAPL, "income", "quarterly")
    assert store._path(AAPL, "income", "quarterly").parts[-3:] == ("statements", "income_quarterly", "AAPL.parquet")
    assert store.load(AAPL, "income", "quarterly").loc[pd.Timestamp("2024-12-31"), "Net Income"] == 36
    cat = store.catalog()
    assert cat.iloc[0]["category"] == "income_quarterly" and cat.iloc[0]["periods"] == 2
