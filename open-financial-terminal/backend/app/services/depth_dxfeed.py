"""dxFeed order-book depth source (Order events → aggregated book).

Streams real order-book data for equities, futures (rates/commodities) and spot FX from a dxFeed
endpoint via the ``dxfeed`` package. Optional + gated: needs the ``dxfeed`` package AND an
authorized endpoint address in ``OFT_DXFEED_ADDRESS`` (a token endpoint, or ``demo.dxfeed.com:7300``
for the delayed demo feed); otherwise ``enabled()`` is False and the widget shows an empty book.

dxFeed delivers depth as individual ``Order`` events keyed by ``index`` (add/update/remove), not
ready-made snapshots — so this keeps a per-symbol order map and rebuilds aggregated price levels
(top ``BOOK_DEPTH`` per side) on each batch before pushing a BookFrame to the hub sink.

Symbology varies by dxFeed contract: equities are plain tickers, spot FX pairs are ``EUR/USD``, and
futures use a ``/ROOT`` continuous form (e.g. ``/ZN``, ``/GC``) — adjust ``_dx_symbol`` to match your
feed's conventions. Untested against a live dxFeed endpoint in this checkout.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

from app.services.realtime import BOOK_DEPTH

log = logging.getLogger("oft.depth.dxfeed")


def _address() -> str:
    # Prefer an address saved in Settings → Data Providers (encrypted on disk), then the env var.
    from app.config import get_provider_secret

    return (get_provider_secret("dxfeed", "address") or os.getenv("OFT_DXFEED_ADDRESS", "")).strip()


def _import_dx() -> Any:
    try:
        import dxfeed as dx  # type: ignore
        return dx
    except Exception:  # noqa: BLE001
        return None


def _dx_symbol(asset: str, symbol: str) -> str:
    """Clean id → dxFeed symbol (best-effort; tune per your feed's symbology)."""
    a, s = asset.lower(), symbol.upper()
    if a in ("rates", "commodity"):
        root = s.split("/")[0].split()[0]
        return f"/{root}"          # continuous future
    return s                        # equity ticker / 'EUR/USD' spot pair


def _is_bid(side: Any) -> bool:
    """dxFeed OrderSide → bid? Handles 'Buy'/'Sell', 'B'/'S', and numeric encodings."""
    v = str(side).upper()
    return v.startswith("B") or v in ("1",)


def _levels(orders: dict[int, tuple[bool, float, float]], *, bid: bool) -> list[list[float]]:
    """Aggregate live orders into sorted price levels (bids desc / asks asc), capped at BOOK_DEPTH."""
    agg: dict[float, float] = {}
    for is_bid, price, size in orders.values():
        if is_bid != bid:
            continue
        agg[price] = agg.get(price, 0.0) + size
    rows = sorted(agg.items(), key=lambda kv: kv[0], reverse=bid)
    return [[p, s] for p, s in rows[:BOOK_DEPTH]]


class DxFeedDepthSource:
    """Maintains a per-symbol order map from dxFeed Order events; emits aggregated BookFrames."""

    def __init__(self) -> None:
        self._dx: Any = None
        self._endpoint: Any = None
        self._subs: dict[tuple[str, str], Any] = {}

    def enabled(self, asset: str) -> bool:
        return (_import_dx() is not None and bool(_address())
                and asset.lower() in ("equity", "rates", "fx", "commodity"))

    def _ensure_endpoint(self) -> Any:
        if self._endpoint is not None:
            return self._endpoint
        self._dx = _import_dx()
        if self._dx is None or not _address():
            return None
        self._endpoint = self._dx.Endpoint(_address())
        return self._endpoint

    def subscribe(self, asset: str, symbol: str, on_book: Any, on_status: Any) -> None:
        dx = _import_dx()
        if dx is None:
            on_status({"state": "unavailable", "error": "dxfeed package not installed"})
            return
        if not _address():
            on_status({"state": "unavailable", "error": "OFT_DXFEED_ADDRESS not set"})
            return
        try:
            endpoint = self._ensure_endpoint()
            if endpoint is None:
                on_status({"state": "unavailable", "error": "dxFeed endpoint unavailable"})
                return
            sym = _dx_symbol(asset, symbol)
            orders: dict[int, tuple[bool, float, float]] = {}

            class _Handler(dx.EventHandler):  # type: ignore[misc]
                def update(self, events: Any) -> None:
                    for ev in events:
                        idx = getattr(ev, "index", None)
                        if idx is None:
                            continue
                        size = getattr(ev, "size", 0.0)
                        price = getattr(ev, "price", float("nan"))
                        if not size or (isinstance(size, float) and math.isnan(size)) or math.isnan(price):
                            orders.pop(idx, None)          # remove / empty order
                        else:
                            orders[idx] = (_is_bid(getattr(ev, "side", "")), float(price), float(size))
                    bids = _levels(orders, bid=True)
                    asks = _levels(orders, bid=False)
                    if bids and asks:
                        on_book({"bids": bids, "asks": asks, "ts": None})

            sub = endpoint.create_subscription("Order")
            sub.set_event_handler(_Handler())
            sub.add_symbols([sym])
            self._subs[(asset, symbol)] = sub
        except Exception as e:  # noqa: BLE001 - connect / auth / subscribe error
            on_status({"state": "reconnecting", "error": str(e)})

    def unsubscribe(self, asset: str, symbol: str) -> None:
        sub = self._subs.pop((asset, symbol), None)
        if sub is not None:
            try:
                sub.close()
            except Exception:  # noqa: BLE001 - best-effort
                pass

    async def reset(self) -> None:
        await self.close()

    async def close(self) -> None:
        for sub in self._subs.values():
            try:
                sub.close()
            except Exception:  # noqa: BLE001 - best-effort
                pass
        self._subs.clear()
        if self._endpoint is not None:
            try:
                self._endpoint.close()
            except Exception:  # noqa: BLE001 - best-effort
                pass
        self._endpoint = None
