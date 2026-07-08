"""Offline tests for the equity options provider (yfinance monkeypatched) + OptionsStore."""

from __future__ import annotations

import pandas as pd
import yfinance

from qhfi.data.options import OptionsStore
from qhfi.data.providers.options_yfinance import OptionsProvider


def _leg(strikes):
    return pd.DataFrame({
        "contractSymbol": [f"AAPL{s}" for s in strikes], "strike": strikes,
        "lastPrice": [1.0] * len(strikes), "bid": [0.9] * len(strikes), "ask": [1.1] * len(strikes),
        "volume": [10] * len(strikes), "openInterest": [100] * len(strikes),
        "impliedVolatility": [0.25] * len(strikes), "inTheMoney": [True] * len(strikes),
    })


class _FakeChain:
    def __init__(self):
        self.calls, self.puts = _leg([100, 105]), _leg([95, 100])


class _FakeTicker:
    options = ("2026-06-12", "2026-06-19", "2026-07-17")

    def option_chain(self, _exp):
        return _FakeChain()


def test_fetch_chain_is_tidy_long_table(monkeypatch):
    monkeypatch.setattr(yfinance, "Ticker", lambda _t: _FakeTicker())
    chain = OptionsProvider().fetch_chain("AAPL", max_expiries=2)
    assert set(chain["type"]) == {"call", "put"}
    assert {"strike", "bid", "ask", "implied_vol", "open_interest", "expiry", "ticker"} <= set(chain.columns)
    assert chain["expiry"].nunique() == 2                 # capped at max_expiries
    assert len(chain) == 2 * 2 * 2                          # 2 expiries × (2 calls + 2 puts)


def test_options_store_partition_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(yfinance, "Ticker", lambda _t: _FakeTicker())
    store = OptionsStore(tmp_path)
    chain = OptionsProvider().fetch_chain("AAPL", max_expiries=1)
    store.save_chain("AAPL", chain, snapshot_ts=pd.Timestamp("2026-06-05", tz="UTC"))

    assert store._path("AAPL").parts[-4:] == ("options", "equity", "yfinance", "AAPL.parquet")
    df = store.load("AAPL")
    assert (df["source"] == "yfinance").all() and df["snapshot_ts"].nunique() == 1
    cat = store.catalog()
    assert cat.iloc[0]["asset_class"] == "equity" and cat.iloc[0]["source"] == "yfinance"
