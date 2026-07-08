"""Pull current equity option chains for a curated set of liquid underlyings.

SNAPSHOT only: yfinance serves the live chain, not history. Each run appends a timestamped
snapshot to lake/options/equity/yfinance/<ticker>.parquet. For backtestable historical option
panels you need a vendor (ORATS / OptionMetrics / CBOE / Databento) — this is data capture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qhfi.data.options import OptionsStore
from qhfi.data.providers.options_yfinance import OptionsProvider

LAKE = Path(__file__).resolve().parents[1] / "data" / "lake"

# Liquid, deeply-traded option underlyings: broad-market ETFs + mega-cap single names.
UNDERLYINGS = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "NFLX", "AVGO",
]


def main(max_expiries: int = 8) -> None:
    provider, store = OptionsProvider(), OptionsStore(LAKE)
    snap = pd.Timestamp.now("UTC")
    print(f"snapshot_ts={snap.isoformat()}  underlyings={len(UNDERLYINGS)}  max_expiries={max_expiries}\n")

    ok = 0
    for t in UNDERLYINGS:
        try:
            chain = provider.fetch_chain(t, max_expiries=max_expiries)
        except Exception as e:                                  # noqa: BLE001 — network/vendor flakiness
            print(f"  {t:6s}  ERROR  {type(e).__name__}: {e}")
            continue
        if chain.empty:
            print(f"  {t:6s}  (no chain returned)")
            continue
        store.save_chain(t, chain, snapshot_ts=snap)
        ok += 1
        print(f"  {t:6s}  {len(chain):5d} contracts  {chain['expiry'].nunique()} expiries")

    print(f"\nsaved {ok}/{len(UNDERLYINGS)} underlyings -> {store.data_dir}")
    cat = store.catalog()
    if not cat.empty:
        print("\ncatalog:")
        print(cat.to_string(index=False))


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
