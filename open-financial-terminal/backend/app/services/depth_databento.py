"""Databento order-book depth source (Live MBP-10).

Streams real 10-level market depth for equities and futures (rates, commodities, and FX **futures** —
Databento has no spot-FX book). Optional + gated: needs the ``databento`` package AND an API key in
``DATABENTO_API_KEY`` (or ``OFT_DATABENTO_API_KEY``); otherwise ``enabled()`` is False and the widget
shows an empty book.

Routing per asset class:
  * equity            → dataset ``XNAS.ITCH`` (Nasdaq TotalView), by raw symbol
  * rates / commodity → dataset ``GLBX.MDP3`` (CME Globex), front-month continuous ``<ROOT>.c.0``
  * fx (spot pair)    → the CME FX **future** on ``GLBX.MDP3`` (EUR/USD → ``6E.c.0``)

Databento's Live client runs its own background thread (``add_callback`` + ``start``), so one client
per (asset, symbol) subscription bridges MBP-10 snapshots into the hub sink. Prices are int9 fixed
point (÷1e9); the INT64_MAX sentinel marks an empty level. Untested against a live key here.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.services.realtime import BOOK_DEPTH

log = logging.getLogger("oft.depth.databento")

_UNDEF = 9223372036854775807  # INT64_MAX — Databento "undefined price" sentinel

# spot pair (or base ccy) → CME FX future root
_FX_FUT = {
    "EUR/USD": "6E", "GBP/USD": "6B", "AUD/USD": "6A", "USD/CAD": "6C", "USD/CHF": "6S",
    "USD/JPY": "6J", "NZD/USD": "6N", "EUR": "6E", "GBP": "6B", "AUD": "6A", "JPY": "6J",
}


def _api_key() -> str:
    # Prefer a key saved in Settings → Data Providers (encrypted on disk), then the env vars.
    from app.config import get_provider_secret

    return (get_provider_secret("databento", "api_key")
            or os.getenv("DATABENTO_API_KEY") or os.getenv("OFT_DATABENTO_API_KEY") or "")


def _import_db() -> Any:
    try:
        import databento as db  # type: ignore
        return db
    except Exception:  # noqa: BLE001
        return None


def _route(asset: str, symbol: str) -> tuple[str, str, str] | None:
    """(dataset, stype_in, resolved_symbol) for the asset/symbol, or None if unsupported."""
    a, s = asset.lower(), symbol.upper()
    if a == "equity":
        return "XNAS.ITCH", "raw_symbol", s
    if a == "fx":
        root = _FX_FUT.get(s) or _FX_FUT.get(s.split("/")[0], "6E")
        return "GLBX.MDP3", "continuous", f"{root}.c.0"
    if a in ("rates", "commodity"):
        return "GLBX.MDP3", "continuous", f"{s.split('/')[0]}.c.0"
    return None


class DatabentoDepthSource:
    """One Databento Live client per subscription; maps MBP-10 records → BookFrame sinks."""

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], Any] = {}

    def enabled(self, asset: str) -> bool:
        return (_import_db() is not None and bool(_api_key())
                and asset.lower() in ("equity", "rates", "fx", "commodity"))

    def subscribe(self, asset: str, symbol: str, on_book: Any, on_status: Any) -> None:
        db = _import_db()
        if db is None:
            on_status({"state": "unavailable", "error": "databento package not installed"})
            return
        key = _api_key()
        if not key:
            on_status({"state": "unavailable", "error": "DATABENTO_API_KEY not set"})
            return
        route = _route(asset, symbol)
        if route is None:
            on_status({"state": "unavailable", "error": f"no Databento route for {asset} {symbol}"})
            return
        dataset, stype, sym = route
        try:
            client = db.Live(key=key)
            client.subscribe(dataset=dataset, schema="mbp-10", stype_in=stype, symbols=[sym])

            def _cb(record: Any) -> None:
                levels = getattr(record, "levels", None)
                if not levels:            # trades/other records carry no book levels
                    return
                bids, asks = [], []
                for lv in levels[:BOOK_DEPTH]:
                    if lv.bid_px != _UNDEF and lv.bid_sz:
                        bids.append([lv.bid_px / 1e9, float(lv.bid_sz)])
                    if lv.ask_px != _UNDEF and lv.ask_sz:
                        asks.append([lv.ask_px / 1e9, float(lv.ask_sz)])
                if bids and asks:
                    ts = getattr(record, "ts_event", None)
                    on_book({"bids": bids, "asks": asks, "ts": int(ts / 1e6) if ts else None})

            client.add_callback(_cb)
            client.start()               # spins up the client's own background thread
            self._clients[(asset, symbol)] = client
        except Exception as e:  # noqa: BLE001 - auth / subscribe / dataset error
            on_status({"state": "reconnecting", "error": str(e)})

    def unsubscribe(self, asset: str, symbol: str) -> None:
        client = self._clients.pop((asset, symbol), None)
        if client is not None:
            try:
                client.stop()
            except Exception:  # noqa: BLE001 - best-effort
                pass

    async def reset(self) -> None:
        return

    async def close(self) -> None:
        for client in self._clients.values():
            try:
                client.stop()
            except Exception:  # noqa: BLE001 - best-effort
                pass
        self._clients.clear()
