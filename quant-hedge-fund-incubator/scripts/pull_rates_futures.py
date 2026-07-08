"""Pull the CME Treasury futures complex (continuous front-month, daily OHLCV) from yfinance
into the lake under market/rates/. AssetClass.RATES + InstrumentForm.FUTURE.

  .venv\\Scripts\\python.exe scripts\\pull_rates_futures.py
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

POOL = "config/instruments/rates_futures.yaml"
YF_TICKER = {"ZT": "ZT=F", "ZF": "ZF=F", "ZN": "ZN=F", "ZB": "ZB=F", "UB": "UB=F", "ZQ": "ZQ=F"}
NAMES = {"ZT": "2Y T-Note", "ZF": "5Y T-Note", "ZN": "10Y T-Note", "ZB": "30Y T-Bond",
         "UB": "Ultra Bond", "ZQ": "30D Fed Funds"}


def main() -> None:
    universe = load_universe(POOL)
    provider = YFinanceDataProvider()                      # serves any yfinance symbol via id
    store = market_store()
    span = DateRange(start=date(2000, 1, 1), end=date.today() + timedelta(days=1))
    print(f"Rates futures → {store.data_dir.resolve()}\n")

    saved = 0
    for ins in universe.instruments:
        # download under the yfinance ticker, store under the clean CME root id
        bars = provider.fetch_daily(Instrument(id=YF_TICKER[ins.id], asset_class=AssetClass.RATES), span)
        if bars.empty:
            print(f"  {ins.id:<3} {NAMES[ins.id]:<14} — unavailable")
            continue
        store.save(ins, bars)
        saved += 1
        print(f"  {ins.id:<3} {NAMES[ins.id]:<14} {len(bars):>5} bars  "
              f"{bars.index.min().date()}→{bars.index.max().date()}  "
              f"last {bars['close'].iloc[-1]:.3f}  mult ${ins.contract_multiplier:.0f}  mod_dur {ins.modified_duration}")

    panel = store.load_panel(universe.instruments, "close")
    print(f"\nDONE: {saved}/{len(universe.instruments)} stored at market/rates/  "
          f"| panel {panel.shape[0]} days × {panel.shape[1]} futures")
    print(f"Latest prices: {panel.ffill().iloc[-1].round(3).to_dict()}")
    print("\nThese are DV01-sized rates instruments (is_margined=True, RiskBasis.DV01) — they slot")
    print("into the FICC engine. (Continuous front-month; full contract ladder needs a vendor.)")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
