"""Compare EDGAR (SEC primary-source, filing-date stamped) vs yfinance (third-party, period-end
stamped) for the overlapping fundamental metrics: history depth + value agreement."""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from qhfi.core.universe_io import load_universe
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.lake import lake_root

PAIRS = [("net_income", "edgar_net_income"), ("revenue", "edgar_revenue")]


def main() -> None:
    # yfinance fundamentals were pulled for the Dow 30; EDGAR for all of equity_sectors.
    universe = load_universe("config/instruments/dow30.yaml")
    fund = FundamentalsStore(lake_root())

    for yf_m, ed_m in PAIRS:
        rows = []
        for ins in universe.instruments:
            if fund.has(ins, yf_m) and fund.has(ins, ed_m):
                yf, ed = fund.load(ins, yf_m), fund.load(ins, ed_m)
                if len(yf) and len(ed):
                    rows.append((ins.id, len(yf), len(ed), yf.iloc[-1], ed.iloc[-1]))
        if not rows:
            print(f"\n{yf_m}: no overlapping names stored.")
            continue

        ids, yfn, edn, yfv, edv = zip(*rows)
        reldiff = np.abs(np.array(yfv) - np.array(edv)) / np.abs(np.array(edv)).clip(1)
        print(f"\n=== {yf_m}  ({len(rows)} names) ===")
        print(f"  history depth (data points):  yfinance avg {np.mean(yfn):.1f}  |  EDGAR avg {np.mean(edn):.1f}")
        print(f"  latest-value agreement vs EDGAR:  within 1%: {(reldiff<0.01).sum()}/{len(rows)}  "
              f"within 5%: {(reldiff<0.05).sum()}/{len(rows)}  median diff {np.median(reldiff):.2%}")
        worst = sorted(zip(ids, reldiff, yfv, edv), key=lambda t: -t[1])[:4]
        print("  largest gaps (id, diff, yfinance, EDGAR):")
        for i, d, y, e in worst:
            print(f"    {i:<6} {d:6.1%}   yf={y/1e9:8.2f}B   edgar={e/1e9:8.2f}B")


if __name__ == "__main__":
    main()
