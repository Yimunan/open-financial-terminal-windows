"""Pull a current L2 order-book snapshot for the top-10 liquid OKX markets.
(Historical order books aren't available from REST — this is the live snapshot.)

  .venv\\Scripts\\python.exe scripts\\pull_orderbook.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _crypto_top10 import EXCHANGE, exchange, top10

from qhfi.data.highfreq import OrderBookStore
from qhfi.data.lake import lake_root


def main() -> None:
    ex = exchange()
    syms = top10(ex)
    store = OrderBookStore(lake_root())
    print(f"Order-book snapshots → {store.data_dir.resolve()}/crypto/{EXCHANGE}\n")
    for sym in syms:
        ob = ex.fetch_order_book(sym, limit=50)
        n = store.save_snapshot(sym, ob, asset_class="crypto", source=EXCHANGE)
        bid, ask = ob["bids"][0][0], ob["asks"][0][0]
        spread_bps = (ask - bid) / bid * 1e4
        print(f"  {sym:<12} {n} levels  best bid {bid:.4g} / ask {ask:.4g}  spread {spread_bps:.1f}bps")
    print("\nDone. (Snapshots only — re-run to append more; historical depth needs a vendor.)")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
