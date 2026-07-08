"""Data-management lifecycle on real data: fetch → validate → persist → incremental refresh
→ catalog → panel. Demonstrates that a second run is a no-op (already cached).

  .venv\\Scripts\\python.exe scripts\\demo_data.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.types import AssetClass, DateRange, Instrument, Universe
from qhfi.data.lake import market_store
from qhfi.data.manager import DataManager
from qhfi.data.providers.crypto_ccxt import CcxtDataProvider

SPAN = DateRange(start=date(2024, 6, 1), end=date(2026, 6, 1))
BASES = ["BTC", "ETH", "SOL", "LTC", "ADA"]
EXCHANGES = [("binance", "USDT"), ("kraken", "USD"), ("coinbase", "USD")]


def discover():
    """Find a reachable exchange and build (provider, universe)."""
    probe = DateRange(start=SPAN.end - timedelta(days=10), end=SPAN.end)
    for exch, quote in EXCHANGES:
        provider = CcxtDataProvider(exchange=exch)
        try:
            test = provider.fetch_daily(Instrument(id=f"BTC/{quote}", asset_class=AssetClass.CRYPTO), probe)
            if len(test):
                instruments = [Instrument(id=f"{b}/{quote}", asset_class=AssetClass.CRYPTO, exchange=exch)
                               for b in BASES]
                return provider, Universe(name=f"crypto@{exch}", instruments=instruments), exch
        except Exception as e:  # noqa: BLE001
            print(f"  {exch}: unavailable ({type(e).__name__})")
    return None


def summarize(reports, label):
    fetched = sum(r.fetched_rows for r in reports)
    skipped = [r for r in reports if r.skipped_reason]
    print(f"\n{label}: {fetched} rows fetched across {len(reports)} instruments; "
          f"{len(skipped)} skipped")
    for r in reports:
        status = r.skipped_reason or f"{r.fetched_rows} rows {r.span_fetched}"
        flags = f"  ⚠ {r.issues}" if r.issues else ""
        print(f"  {r.instrument_id:<10} {status}{flags}")


def main() -> None:
    found = discover()
    if found is None:
        print("No exchange reachable; aborting (this demo needs live data).")
        return
    provider, universe, exch = found

    store = market_store()
    mgr = DataManager(store, {AssetClass.CRYPTO: provider}, max_daily_return=0.6)
    print(f"Data lake: {store.data_dir.resolve()}   |   source: {exch}")

    summarize(mgr.update(universe, SPAN), "First update (cold)")
    summarize(mgr.update(universe, SPAN), "Second update (warm — should be no-op)")

    print("\nCatalog:")
    print(mgr.catalog().to_string(index=False))

    panel = mgr.get_panel(universe, "close", SPAN)
    print(f"\nPanel: {panel.shape[0]} days × {panel.shape[1]} instruments  "
          f"({panel.index.min().date()} → {panel.index.max().date()})")
    print(f"NaNs per instrument: {panel.isna().sum().to_dict()}")


if __name__ == "__main__":
    main()
