"""Pull the commodity futures complex (continuous front-month, daily OHLCV) from yfinance into
the lake under market/commodity/. AssetClass.COMMODITY + InstrumentForm.FUTURE.

  .venv\\Scripts\\python.exe scripts\\pull_commodities.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.types import AssetClass, DateRange, Instrument
from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider

POOL = "config/instruments/commodities.yaml"
# clean CME/ICE root id → yfinance continuous front-month ticker
YF_TICKER = {c: f"{c}=F" for c in
             ["GC", "SI", "HG", "PL", "PA", "CL", "BZ", "NG", "RB", "HO",
              "ZC", "ZW", "ZS", "KC", "SB", "CT", "CC", "LE", "HE"]}
NAMES = {"GC": "Gold", "SI": "Silver", "HG": "Copper", "PL": "Platinum", "PA": "Palladium",
         "CL": "WTI Crude", "BZ": "Brent Crude", "NG": "Nat Gas", "RB": "Gasoline", "HO": "Heating Oil",
         "ZC": "Corn", "ZW": "Wheat", "ZS": "Soybeans", "KC": "Coffee", "SB": "Sugar #11",
         "CT": "Cotton", "CC": "Cocoa", "LE": "Live Cattle", "HE": "Lean Hogs"}


def main() -> None:
    universe = load_universe(POOL)
    provider = YFinanceDataProvider()                      # serves any yfinance symbol via id
    store = market_store()
    span = DateRange(start=date(1990, 1, 1), end=date.today() + timedelta(days=1))
    print(f"Commodity futures → {store.data_dir.resolve()}\n")

    saved = 0
    for ins in universe.instruments:
        # download under the yfinance =F ticker, store under the clean root id
        bars = provider.fetch_daily(Instrument(id=YF_TICKER[ins.id], asset_class=AssetClass.COMMODITY), span)
        if bars.empty:
            print(f"  {ins.id:<3} {NAMES[ins.id]:<14} — unavailable")
            continue
        store.save(ins, bars)
        saved += 1
        print(f"  {ins.id:<3} {NAMES[ins.id]:<14} {len(bars):>5} bars  "
              f"{bars.index.min().date()}→{bars.index.max().date()}  "
              f"last {bars['close'].iloc[-1]:.2f}  mult {ins.contract_multiplier:.0f}  [{ins.exchange}]")

    panel = store.load_panel(universe.instruments, "close")
    print(f"\nDONE: {saved}/{len(universe.instruments)} stored at market/commodity/  "
          f"| panel {panel.shape[0]} days × {panel.shape[1]} futures")
    print("\nMargined notional-risk instruments (is_margined=True, RiskBasis.NOTIONAL, CMES "
          "calendar) — they slot into the FICC engine. Continuous front-month; full contract "
          "ladder + roll-yield/carry needs a vendor.")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
