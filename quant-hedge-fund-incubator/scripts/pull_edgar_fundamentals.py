"""Phases 3-4: pull SEC XBRL companyfacts for a universe, cache the tidy facts (`xbrl`
category), and extract point-in-time fundamental metrics (stamped at filing date) into the
`fundamental` category — the primary-source replacement for the yfinance fundamentals.

  .venv\\Scripts\\python.exe scripts\\pull_edgar_fundamentals.py [pool.yaml]
  set SEC_USER_AGENT="Your Name your@email.com"
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from qhfi.core.universe_io import load_universe
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.fundamentals_edgar import METRICS, pit_metric
from qhfi.data.lake import lake_root
from qhfi.data.providers.edgar import EdgarClient
from qhfi.data.xbrl import XbrlStore, tidy_facts

POOL = sys.argv[1] if len(sys.argv) > 1 else "config/instruments/equity_sectors.yaml"


def main() -> None:
    universe = load_universe(POOL)
    edgar = EdgarClient()
    xbrl = XbrlStore(lake_root())
    fund = FundamentalsStore(lake_root())
    print(f"EDGAR XBRL → fundamentals for {len(universe.instruments)} names\n")

    facts_saved = metric_points = errors = 0
    per_metric = {m: 0 for m in METRICS}
    for i, ins in enumerate(universe.instruments, 1):
        try:
            cik = edgar.ticker_to_cik(ins.id)
            tidy = tidy_facts(edgar.company_facts(cik))
            xbrl.save(ins.id, tidy)
            facts_saved += 1
            for metric in METRICS:
                s = pit_metric(tidy, metric)
                if len(s):
                    fund.save(ins, f"edgar_{metric}", s)
                    metric_points += len(s)
                    per_metric[metric] += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            if errors <= 12:
                print(f"  {ins.id}: {type(e).__name__} {str(e)[:80]}")
        if i % 25 == 0:
            print(f"  [{i}/{len(universe.instruments)}] facts={facts_saved} points={metric_points} errors={errors}", flush=True)

    print(f"\nDONE: {facts_saved} companies' XBRL cached · {metric_points} PIT metric points · {errors} errors")
    print("coverage per metric:", {m: per_metric[m] for m in METRICS})

    # demo: AAPL net income, PIT (filed-date stamped), vs yfinance
    from qhfi.core.types import AssetClass, Instrument
    aapl = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)
    if fund.has(aapl, "edgar_net_income"):
        s = fund.load(aapl, "edgar_net_income").tail(5)
        print("\nAAPL net income (EDGAR, point-in-time — index is FILING date):")
        for dt, v in s.items():
            print(f"  filed {dt.date()}  ${v/1e9:.2f}B")
        if fund.has(aapl, "net_income"):
            print(f"  (yfinance latest net_income for comparison: "
                  f"${fund.load(aapl, 'net_income').iloc[-1]/1e9:.2f}B, period-end stamped)")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
