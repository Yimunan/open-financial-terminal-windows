"""Pull daily OHLC for major market BENCHMARK indices (US broad + volatility + global) from
yfinance into lake/reference/index/yfinance/.

Indices are reference/benchmark series (not tradable instruments) — used to benchmark strategy
returns, build relative-strength factors, and as risk-regime signals (VIX/MOVE). yfinance serves
them under caret tickers (^GSPC); we store under the clean symbol (GSPC.parquet).

  .venv\\Scripts\\python.exe scripts\\pull_indices.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.types import AssetClass, DateRange, Instrument
from qhfi.data.indices import IndexStore
from qhfi.data.lake import lake_root
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider

# (yfinance ticker, name, group) — caret tickers; stored under the clean symbol.
INDICES = [
    # US broad equity
    ("^GSPC", "S&P 500", "us_equity"),
    ("^DJI", "Dow Jones Industrial", "us_equity"),
    ("^IXIC", "Nasdaq Composite", "us_equity"),
    ("^NDX", "Nasdaq 100", "us_equity"),
    ("^RUT", "Russell 2000", "us_equity"),
    ("^NYA", "NYSE Composite", "us_equity"),
    # Volatility / risk regime
    ("^VIX", "CBOE Volatility (S&P)", "volatility"),
    ("^VXN", "CBOE Volatility (Nasdaq)", "volatility"),
    ("^MOVE", "ICE BofA Treasury Vol", "volatility"),
    # Global equity
    ("^FTSE", "FTSE 100 (UK)", "global_equity"),
    ("^GDAXI", "DAX (Germany)", "global_equity"),
    ("^FCHI", "CAC 40 (France)", "global_equity"),
    ("^STOXX50E", "Euro Stoxx 50", "global_equity"),
    ("^N225", "Nikkei 225 (Japan)", "global_equity"),
    ("^HSI", "Hang Seng (Hong Kong)", "global_equity"),
    ("^AXJO", "ASX 200 (Australia)", "global_equity"),
    ("^GSPTSE", "S&P/TSX (Canada)", "global_equity"),
    ("^BSESN", "BSE Sensex (India)", "global_equity"),
]


def main() -> None:
    provider, store = YFinanceDataProvider(), IndexStore(lake_root())
    span = DateRange(start=date(1990, 1, 1), end=date.today() + timedelta(days=1))
    print(f"Index benchmarks → {store.data_dir.resolve()}\n")

    saved = 0
    for tkr, name, group in INDICES:
        # download under the caret ticker (asset_class is just to satisfy the fetch contract).
        bars = provider.fetch_daily(Instrument(id=tkr, asset_class=AssetClass.EQUITY), span)
        if bars.empty:
            print(f"  {tkr:<10} {name:<26} — unavailable")
            continue
        store.save(tkr, bars)
        saved += 1
        print(f"  {tkr:<10} {name:<26} {len(bars):>5} bars  "
              f"{bars.index.min().date()}→{bars.index.max().date()}  last {bars['close'].iloc[-1]:.1f}  [{group}]")

    cat = store.catalog()
    print(f"\nDONE: {saved}/{len(INDICES)} indices stored at reference/index/  ({len(cat)} files)")
    print("\nBenchmarks/reference — not tradable instruments (trade ETFs/futures on them). VIX/MOVE "
          "are risk-regime signals. Index volume is meaningless (yfinance reports 0).")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
