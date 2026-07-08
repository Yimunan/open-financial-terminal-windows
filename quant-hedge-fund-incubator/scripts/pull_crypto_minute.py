"""Pull 1-minute OHLCV for the top-10 liquid OKX markets into lake/intraday_1m/.
Resumable: re-running continues each symbol from its last stored bar. Run in the background.

Look-back is configurable (default 365 days); OKX serves only as much 1m history as it has, so
each symbol stops when the exchange runs out (newer coins have shorter history):
  .venv\\Scripts\\python.exe scripts\\pull_crypto_minute.py            # 365 days
  .venv\\Scripts\\python.exe scripts\\pull_crypto_minute.py 1825       # 5 years
  QHFI_CRYPTO_DAYS=1825 .venv\\Scripts\\python.exe scripts\\pull_crypto_minute.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
from _crypto_top10 import EXCHANGE, exchange, top10

from qhfi.data.highfreq import MinuteBarStore
from qhfi.data.lake import lake_root

MINUTE_MS = 60_000
PAGE = 100


def lookback_days() -> int:
    """Look-back in days: CLI arg wins, else QHFI_CRYPTO_DAYS env, else 365."""
    if len(sys.argv) > 1:
        return int(sys.argv[1])
    return int(os.environ.get("QHFI_CRYPTO_DAYS", "365"))


MAX_RETRIES = 8   # consecutive failures before giving up on this symbol (don't hang forever)


def _pull_range(ex, symbol: str, store: MinuteBarStore, since: int, stop_ms: int) -> int:
    """Page 1m bars forward from `since` until `stop_ms` (or the exchange runs out).

    Retries transient errors with capped exponential backoff; after MAX_RETRIES consecutive
    failures it flushes what it has and stops (so a persistent throttle/ban can't hang the run).
    """
    fetched, buf, errors = 0, [], 0
    while since < stop_ms:
        try:
            batch = ex.fetch_ohlcv(symbol, "1m", since=since, limit=PAGE)
            errors = 0
        except Exception as e:  # noqa: BLE001 - transient → capped backoff, then give up
            errors += 1
            if errors >= MAX_RETRIES:
                print(f"    {symbol}: giving up after {errors} consecutive errors ({type(e).__name__})", flush=True)
                break
            time.sleep(min(2.0 * 2 ** (errors - 1), 60.0))
            continue
        if not batch:
            break
        batch = [b for b in batch if b[0] < stop_ms]    # don't overshoot the window
        if batch:
            buf.extend(batch)
            fetched += len(batch)
        last = (batch[-1][0] if batch else since)
        since = last + MINUTE_MS
        if len(buf) >= 20_000:                          # periodic flush (crash-safe progress)
            _flush(store, symbol, buf); buf = []
        if not batch:                                   # window exhausted / caught up to head
            break
    if buf:
        _flush(store, symbol, buf)
    return fetched


def pull_symbol(ex, symbol: str, store: MinuteBarStore, start_ms: int, now_ms: int) -> int:
    """Two passes so an existing 1y cache extends to a 5y window: backfill the older gap
    [start_ms, earliest_stored) AND extend forward [last_stored, now). Fresh symbols just
    pull [start_ms, now) in the forward pass."""
    fetched = 0
    first = store.first_ms(symbol, source=EXCHANGE)
    if first is not None and start_ms < first:          # backfill older history
        fetched += _pull_range(ex, symbol, store, start_ms, first)
    last = store.last_ms(symbol, source=EXCHANGE)
    forward_since = (last + MINUTE_MS) if last else start_ms
    fetched += _pull_range(ex, symbol, store, forward_since, now_ms)
    return fetched


def _flush(store: MinuteBarStore, symbol: str, rows: list[list]) -> None:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts")
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    store.save(symbol, df[["open", "high", "low", "close", "volume"]], source=EXCHANGE)


def main() -> None:
    ex = exchange()
    syms = top10(ex)
    store = MinuteBarStore(lake_root())
    days = lookback_days()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    print(f"1-minute pull (OKX) → {store.data_dir.resolve()}")
    print(f"Look-back: {days} days (~{days/365:.1f}y; OKX caps actual 1m depth per symbol)")
    print(f"Top-10: {syms}\n", flush=True)

    for i, sym in enumerate(syms, 1):
        t0 = time.time()
        n = pull_symbol(ex, sym, store, start_ms, now_ms)
        total = len(store.load(sym, source=EXCHANGE)) if store.has(sym, source=EXCHANGE) else 0
        print(f"  [{i}/10] {sym:<12} +{n:,} bars  (total {total:,})  {time.time()-t0:.0f}s", flush=True)

    cat = store.catalog()
    print(f"\nDONE: {len(cat)} symbols, {cat['bars'].sum():,} total 1m bars")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
