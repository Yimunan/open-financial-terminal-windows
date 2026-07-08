"""Diagnose why EDGAR vs yfinance differ for specific names — inspect the raw XBRL facts the
extractor saw, to find the root cause (concept choice, period duration, fiscal calendar)."""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from qhfi.core.types import AssetClass, Instrument
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.fundamentals_edgar import CONCEPT_MAP, pit_metric
from qhfi.data.lake import lake_root
from qhfi.data.xbrl import XbrlStore

xbrl, fund = XbrlStore(lake_root()), FundamentalsStore(lake_root())


def show(ticker: str, metric: str, yf_metric: str):
    tidy = xbrl.load(ticker)
    print(f"\n### {ticker} — {metric}")
    for concept in CONCEPT_MAP[metric]:
        sub = tidy[tidy["concept"] == concept].copy()
        if sub.empty:
            print(f"  {concept}: ABSENT")
            continue
        sub["filed"] = pd.to_datetime(sub["filed"])
        sub["end"] = pd.to_datetime(sub["end"])
        sub["start"] = pd.to_datetime(sub["start"])
        sub["dur"] = (sub["end"] - sub["start"]).dt.days
        latest = sub.sort_values("filed").tail(3)
        print(f"  {concept}: {len(sub)} facts — latest 3:")
        for _, r in latest.iterrows():
            tag = "QTR" if 80 <= r["dur"] <= 100 else ("YTD/ANN" if r["dur"] > 100 else "?")
            print(f"     {str(r['start'].date())}..{str(r['end'].date())} dur={r['dur']:>3}d "
                  f"[{tag}]  ${r['val']/1e9:7.2f}B  filed {r['filed'].date()}")
    s = pit_metric(tidy, metric)
    ins = Instrument(id=ticker, asset_class=AssetClass.EQUITY)
    yf = fund.load(ins, yf_metric).iloc[-1] if fund.has(ins, yf_metric) else float("nan")
    print(f"  → EDGAR pit_metric latest: ${s.iloc[-1]/1e9:.2f}B   |   yfinance: ${yf/1e9:.2f}B")


def main() -> None:
    show("NVDA", "revenue", "revenue")
    show("JPM", "revenue", "revenue")
    show("HD", "net_income", "net_income")
    show("NVDA", "net_income", "net_income")


if __name__ == "__main__":
    main()
