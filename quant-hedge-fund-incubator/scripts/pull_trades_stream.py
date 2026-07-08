"""Long-running trade-tape recorder — the crossing flow that fills passive quotes.

Records public trades for the top-10 OKX markets into ``TradeStore``
(``lake/trades/crypto/okx/<symbol>.parquet``, schema ``ts, price, size, side``). This is the
single highest-value dataset for market-making realism: the matcher fills resting quotes
against real prints (and measures adverse selection via markout) instead of inferring fills
from coarse book-snapshot transitions.

  .venv\\Scripts\\python.exe scripts\\pull_trades_stream.py --interval 1.0

REST ``fetch_trades`` returns the most recent prints each poll; we de-dupe on ``ts+price+size``
so overlapping polls don't double-count. For complete tick coverage use ccxt.pro
``watch_trades`` (websocket) — same persistence path. Run durably as a Scheduled Task / NSSM
service; stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
from _crypto_top10 import EXCHANGE, exchange, top10

from qhfi.data.highfreq import TradeStore
from qhfi.data.lake import lake_root

_RUN = True


def _stop(*_a) -> None:
    global _RUN
    _RUN = False
    print("\nstopping… (flushing catalog)", flush=True)


def _to_rows(trades: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ts": int(t["timestamp"]), "price": float(t["price"]),
          "size": float(t["amount"]), "side": str(t.get("side") or "")}
         for t in trades if t.get("timestamp") is not None],
        columns=["ts", "price", "size", "side"],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="stream public trades (the tape) into the lake")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between polls per cycle")
    ap.add_argument("--limit", type=int, default=200, help="recent trades to fetch per poll")
    ap.add_argument("--symbols", default="", help="comma-separated override (else top-10 OKX)")
    ap.add_argument("--refresh-every", type=int, default=300, help="catalog.refresh() cadence (s)")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    ex = exchange()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or top10(ex)
    store = TradeStore(lake_root())
    print(f"trade tape → {store.data_dir.resolve()}/crypto/{EXCHANGE}  ({len(syms)} symbols)\n",
          flush=True)

    total = 0
    last_refresh = time.monotonic()
    backoff = 1.0
    while _RUN:
        cycle = 0
        for sym in syms:
            if not _RUN:
                break
            try:
                trades = ex.fetch_trades(sym, limit=args.limit)
                rows = _to_rows(trades)
                if not rows.empty:
                    before = store.last_ms(sym, source=EXCHANGE) or 0
                    store.save(sym, rows, asset_class="crypto", source=EXCHANGE)
                    cycle += int((rows["ts"] > before).sum())
                backoff = 1.0
            except Exception as e:                                  # noqa: BLE001 - keep streaming
                print(f"  ! {sym}: {type(e).__name__}: {e}  (backoff {backoff:.0f}s)", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        total += cycle
        print(f"  +~{cycle} new prints (total ~{total})", flush=True)

        if time.monotonic() - last_refresh > args.refresh_every:
            from qhfi.data.catalog import refresh
            refresh()
            last_refresh = time.monotonic()
        time.sleep(max(args.interval, 0.0))

    from qhfi.data.catalog import refresh
    refresh()
    print(f"done — ~{total} prints recorded.", flush=True)


if __name__ == "__main__":
    main()
