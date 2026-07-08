"""Pull G10 spot FX (daily OHLC) from yfinance into the lake under market/fx/.

AssetClass.FX + InstrumentForm.CASH (spot, fully funded). yfinance serves FX as 'EURUSD=X';
we download under that symbol and store under the canonical pair id (market/fx/EUR_USD.parquet).
Note FX bars have no real volume (yfinance reports 0) — that column is kept for schema
uniformity but is meaningless for spot FX.

  .venv\\Scripts\\python.exe scripts\\pull_fx.py
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

POOL = "config/instruments/fx_majors.yaml"


def yf_symbol(pair_id: str) -> str:
    """'EUR/USD' -> 'EURUSD=X' (yfinance spot-FX convention)."""
    return pair_id.replace("/", "") + "=X"


def main() -> None:
    universe = load_universe(POOL)
    provider = YFinanceDataProvider()                      # serves any yfinance symbol via id
    store = market_store()
    span = DateRange(start=date(2003, 12, 1), end=date.today() + timedelta(days=1))
    print(f"Spot FX → {store.data_dir.resolve()}\n")

    saved = 0
    for ins in universe.instruments:
        # download under the yfinance =X symbol, store under the clean canonical pair id
        bars = provider.fetch_daily(Instrument(id=yf_symbol(ins.id), asset_class=AssetClass.FX), span)
        if bars.empty:
            print(f"  {ins.id:<8} — unavailable")
            continue
        store.save(ins, bars)
        saved += 1
        print(f"  {ins.id:<8} {len(bars):>5} bars  {bars.index.min().date()}→{bars.index.max().date()}  "
              f"last {bars['close'].iloc[-1]:.4f}")

    panel = store.load_panel(universe.instruments, "close")
    print(f"\nDONE: {saved}/{len(universe.instruments)} stored at market/fx/  "
          f"| panel {panel.shape[0]} days × {panel.shape[1]} pairs")
    print(f"Latest: {panel.ffill().iloc[-1].round(4).to_dict()}")
    print("\nSpot FX = cash/fully-funded (is_margined=False, RiskBasis.NOTIONAL). For carry you "
          "need a rate-differential / forward-points feed; FX forwards (FORWARD form) are a "
          "separate margined instrument not modeled here yet.")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
