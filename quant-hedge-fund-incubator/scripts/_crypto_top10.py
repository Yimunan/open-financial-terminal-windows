"""Shared: pick the top-10 liquid spot crypto markets on OKX (24h quote volume, excluding
stablecoin-vs-stablecoin pairs)."""

from __future__ import annotations

import ccxt

EXCHANGE = "okx"
_STABLES = {"USDT", "USDC", "USDG", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD", "USDE", "BUSD", "EURT"}


def exchange() -> ccxt.Exchange:
    ex = getattr(ccxt, EXCHANGE)({"enableRateLimit": True})
    ex.load_markets()
    return ex


def top10(ex: ccxt.Exchange) -> list[str]:
    tickers = ex.fetch_tickers()
    ranked = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT") or not ex.markets.get(sym, {}).get("spot", True):
            continue
        if sym.split("/")[0] in _STABLES:               # drop stablecoin pairs
            continue
        ranked.append((sym, t.get("quoteVolume") or 0.0))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in ranked[:10]]
