"""Long-running L2 depth recorder — accumulate real order-book history into the lake.

Exchange REST only serves the *current* book, so historical depth must be recorded going
forward. This polls the top-10 OKX markets and appends one snapshot per symbol per interval
into ``OrderBookStore`` (the same long-row schema ``pull_orderbook.py`` writes), so the
market-maker backtest gets genuine OBI/microprice history over time.

  .venv\\Scripts\\python.exe scripts\\pull_orderbook_stream.py --interval 1.0 --levels 20

Run it durably on Windows as a Scheduled Task (at-logon / at-startup) or under NSSM as a
service — it is idempotent (append-only) and self-healing (reconnect with capped backoff).
For higher fidelity (sub-second), swap the REST poll for ccxt.pro ``watch_order_book`` (a true
incremental websocket); the persistence path is unchanged. Stop with Ctrl-C — it flushes the
catalog on the way out.

NOTE: this records book *state*, not the *flow* that fills a passive quote. Pair it with
``pull_trades_stream.py`` (the trade tape) for realistic fill/adverse-selection modelling.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from _crypto_top10 import EXCHANGE, exchange, top10

from qhfi.data.highfreq import OrderBookStore
from qhfi.data.lake import lake_root

_RUN = True


def _stop(*_a) -> None:
    global _RUN
    _RUN = False
    print("\nstopping… (flushing catalog)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="stream L2 order-book snapshots into the lake")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between polls per cycle")
    ap.add_argument("--levels", type=int, default=20, help="depth levels per side to persist")
    ap.add_argument("--symbols", default="", help="comma-separated override (else top-10 OKX)")
    ap.add_argument("--refresh-every", type=int, default=300, help="catalog.refresh() cadence (s)")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    ex = exchange()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or top10(ex)
    store = OrderBookStore(lake_root())
    print(f"L2 stream → {store.data_dir.resolve()}/crypto/{EXCHANGE}  "
          f"({len(syms)} symbols, {args.levels} levels, {args.interval}s)\n", flush=True)

    rows_total = 0
    last_refresh = time.monotonic()
    backoff = 1.0
    while _RUN:
        cycle_rows = 0
        for sym in syms:
            if not _RUN:
                break
            try:
                ob = ex.fetch_order_book(sym, limit=args.levels)
                cycle_rows += store.save_snapshot(sym, ob, asset_class="crypto", source=EXCHANGE)
                backoff = 1.0
            except Exception as e:                                  # noqa: BLE001 - keep streaming
                print(f"  ! {sym}: {type(e).__name__}: {e}  (backoff {backoff:.0f}s)", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        rows_total += cycle_rows
        spread = ""
        try:
            ob = ex.fetch_order_book(syms[0], limit=1)
            bid, ask = ob["bids"][0][0], ob["asks"][0][0]
            spread = f"  {syms[0]} spread {(ask - bid) / bid * 1e4:.1f}bps"
        except Exception:                                           # noqa: BLE001
            pass
        print(f"  +{cycle_rows} rows (total {rows_total}){spread}", flush=True)

        if time.monotonic() - last_refresh > args.refresh_every:
            from qhfi.data.catalog import refresh
            refresh()
            last_refresh = time.monotonic()
        time.sleep(max(args.interval, 0.0))

    from qhfi.data.catalog import refresh
    refresh()
    print(f"done — {rows_total} rows recorded.", flush=True)


if __name__ == "__main__":
    main()
