"""Probe which crypto exchanges are reachable AND serve deep 1-minute history (for a 1-year
pull), and rank top-10 spot pairs by 24h liquidity on the winner."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ccxt

YEAR_AGO_MS = int((datetime.now(timezone.utc) - timedelta(days=365)).timestamp() * 1000)
CANDIDATES = ["binance", "bybit", "okx", "kucoin", "gateio", "mexc", "coinbase", "kraken"]


def main() -> None:
    print("Probing exchanges for reachability + deep 1m history:\n")
    working = []
    for exid in CANDIDATES:
        sym = "BTC/USDT"
        try:
            ex = getattr(ccxt, exid)({"enableRateLimit": True})
            ex.load_markets()
            if sym not in ex.markets:
                sym = "BTC/USD" if "BTC/USD" in ex.markets else next(
                    (m for m in ex.markets if m.startswith("BTC/")), None)
            candles = ex.fetch_ohlcv(sym, "1m", since=YEAR_AGO_MS, limit=10)
            if candles:
                first = datetime.fromtimestamp(candles[0][0] / 1000, tz=timezone.utc)
                age = (datetime.now(timezone.utc) - first).days
                deep = age > 300
                print(f"  {exid:<9} OK  {sym:<10} 1m@1yr-ago → {first.date()} ({age}d old) "
                      f"{'✓ DEEP' if deep else '✗ shallow'}  page≈{len(candles)}+")
                if deep:
                    working.append((exid, sym, ex))
            else:
                print(f"  {exid:<9} reachable but no deep 1m")
        except Exception as e:  # noqa: BLE001
            print(f"  {exid:<9} {type(e).__name__}: {str(e)[:50]}")
        time.sleep(0.4)

    if not working:
        print("\nNo reachable exchange with deep 1m. (binance blocked, kraken shallow.)")
        return

    exid, _, ex = working[0]
    print(f"\n→ Using {exid}. Top 10 spot pairs by 24h quote volume:")
    try:
        tickers = ex.fetch_tickers()
        usdt = [(s, t.get("quoteVolume") or 0) for s, t in tickers.items()
                if s.endswith("/USDT") and ex.markets.get(s, {}).get("spot", True)]
        top = sorted(usdt, key=lambda x: x[1], reverse=True)[:10]
        for s, v in top:
            print(f"  {s:<12} 24h vol ≈ ${v/1e6:,.0f}M")
    except Exception as e:  # noqa: BLE001
        print(f"  ticker ranking failed: {type(e).__name__} {e}")


if __name__ == "__main__":
    main()
