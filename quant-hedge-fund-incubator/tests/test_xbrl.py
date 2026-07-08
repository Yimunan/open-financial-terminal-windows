"""XBRL extraction: tidy_facts flattening, and pit_metric — quarterly-duration selection (drops
YTD), instant balances, and filing-date (PIT) stamping."""

from __future__ import annotations

import pandas as pd

from qhfi.data.fundamentals_edgar import pit_metric
from qhfi.data.xbrl import tidy_facts

_FACTS = {"facts": {"us-gaap": {
    "NetIncomeLoss": {"units": {"USD": [
        {"start": "2025-01-01", "end": "2025-03-31", "val": 100, "fy": 2025, "fp": "Q1",
         "form": "10-Q", "filed": "2025-04-30", "accn": "a1"},
        {"start": "2025-01-01", "end": "2025-06-30", "val": 250, "fy": 2025, "fp": "Q2",   # YTD 6mo
         "form": "10-Q", "filed": "2025-07-30", "accn": "a2"},
        {"start": "2025-04-01", "end": "2025-06-30", "val": 150, "fy": 2025, "fp": "Q2",   # quarterly
         "form": "10-Q", "filed": "2025-07-30", "accn": "a2"},
    ]}},
    "Assets": {"units": {"USD": [
        {"end": "2025-03-31", "val": 1000, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-30", "accn": "a1"},
        {"end": "2025-06-30", "val": 1100, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-07-30", "accn": "a2"},
    ]}},
}}}


def test_tidy_facts_flattens():
    df = tidy_facts(_FACTS)
    assert set(df["concept"]) == {"NetIncomeLoss", "Assets"}
    assert len(df) == 5


def test_pit_flow_selects_quarterly_and_drops_ytd():
    s = pit_metric(tidy_facts(_FACTS), "net_income")
    assert list(s.values) == [100.0, 150.0]                 # Q1=100, Q2 quarterly=150 (NOT 250 YTD)
    assert str(s.index[-1].date()) == "2025-07-30"          # stamped at the FILING date
    assert str(s.index.tz) == "UTC"


def test_pit_instant_balance():
    s = pit_metric(tidy_facts(_FACTS), "total_assets")
    assert list(s.values) == [1000.0, 1100.0]
    assert str(s.index[0].date()) == "2025-04-30"           # filing date, not period end


def test_missing_metric_is_empty():
    assert pit_metric(tidy_facts(_FACTS), "operating_cash_flow").empty
