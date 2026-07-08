"""Alpaca paper broker adapter (US equities) via alpaca-py.

Wraps Alpaca's hosted paper environment behind the ``Broker`` protocol, so the trading loop
submits orders through the exact same interface as the in-process simulator. Requires
``pip install alpaca-py`` and ALPACA_* keys with ALPACA_PAPER=true.

Order placement, in Alpaca terms:
    TradingClient(key, secret, paper=True).submit_order(MarketOrderRequest(
        symbol="AAPL", qty=10, side=OrderSide.BUY, time_in_force=TimeInForce.DAY))

``submit`` below maps our ``Order`` (instrument_id, side, quantity, type, limit_price) plus an
optional stop price / trailing percent onto that. Beyond market/limit it also builds stop,
stop-limit and trailing-stop requests, and exposes close/flatten/cancel/order-history so the OFT
paper router can drive the Alpaca book with the same surface as the local simulator. alpaca-py is
imported lazily so the package isn't required unless you actually trade; inject ``client=`` in
tests to stay offline.
"""

from __future__ import annotations

import os
from typing import Any

from qhfi.execution.base import Account, Broker, Order, OrderSide, Position

# Alpaca order status → our coarse {open, filled, cancelled} buckets (anything terminal-but-unfilled
# is reported as cancelled so the UI doesn't show it as live).
_OPEN_STATUSES = {
    "new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled",
    "pending_cancel", "pending_replace", "calculated", "held", "replaced",
}


def _build_order_request(order: Order, stop_price: float | None = None,
                         trail_pct: float | None = None) -> Any:
    """Translate a qhfi Order into an alpaca-py order request (DAY TIF).

    Supports market / limit / stop / stop_limit / trailing_stop. ``stop_price`` is required for
    stop & stop_limit; ``trail_pct`` (percent) for trailing_stop.
    """
    from alpaca.trading.enums import OrderSide as AlpacaSide
    from alpaca.trading.enums import TimeInForce
    from alpaca.trading.requests import (
        LimitOrderRequest,
        MarketOrderRequest,
        StopLimitOrderRequest,
        StopOrderRequest,
        TrailingStopOrderRequest,
    )

    side = AlpacaSide.BUY if order.side is OrderSide.BUY else AlpacaSide.SELL
    qty = abs(order.quantity)                          # alpaca uses positive qty + side
    common = dict(symbol=order.instrument_id, qty=qty, side=side, time_in_force=TimeInForce.DAY)
    otype = order.type

    if otype == "market":
        return MarketOrderRequest(**common)
    if otype == "limit":
        return LimitOrderRequest(**common, limit_price=order.limit_price)
    if otype == "stop":
        if stop_price is None:
            raise ValueError("stop order needs a stop_price")
        return StopOrderRequest(**common, stop_price=stop_price)
    if otype == "stop_limit":
        if stop_price is None or order.limit_price is None:
            raise ValueError("stop_limit order needs a stop_price and a limit_price")
        return StopLimitOrderRequest(**common, stop_price=stop_price, limit_price=order.limit_price)
    if otype == "trailing_stop":
        if not trail_pct or trail_pct <= 0:
            raise ValueError("trailing_stop order needs trail_pct > 0")
        return TrailingStopOrderRequest(**common, trail_percent=trail_pct)
    raise ValueError(f"unsupported order type: {otype}")


class AlpacaPaperBroker(Broker):
    def __init__(self, api_key: str = "", api_secret: str = "", paper: bool = True,
                 client: Any = None) -> None:
        self.api_key, self.api_secret, self.paper = api_key, api_secret, paper
        self._client = client

    @classmethod
    def from_env(cls) -> AlpacaPaperBroker:
        return cls(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            api_secret=os.environ.get("ALPACA_API_SECRET", ""),
            paper=os.environ.get("ALPACA_PAPER", "true").lower() != "false",
        )

    def _c(self) -> Any:
        if self._client is None:
            from alpaca.trading.client import TradingClient
            self._client = TradingClient(self.api_key, self.api_secret, paper=self.paper)
        return self._client

    def get_positions(self) -> dict[str, Position]:
        return {
            p.symbol: Position(instrument_id=p.symbol, quantity=float(p.qty),
                               avg_price=float(p.avg_entry_price))
            for p in self._c().get_all_positions()
        }

    def get_account(self) -> Account:
        a = self._c().get_account()
        return Account(equity=float(a.equity), cash=float(a.cash), positions=self.get_positions())

    def submit(self, order: Order, stop_price: float | None = None,
               trail_pct: float | None = None) -> str:
        """Place ``order`` on Alpaca; return the broker order id.

        ``stop_price`` / ``trail_pct`` drive the stop / stop-limit / trailing-stop request types.
        """
        submitted = self._c().submit_order(_build_order_request(order, stop_price, trail_pct))
        return str(submitted.id)

    # ── advanced ops (parity with the local SimBroker surface) ───────────────────
    def close_position(self, symbol: str) -> str | None:
        """Market-close the whole position in ``symbol``; return the order id, or None if flat."""
        from alpaca.common.exceptions import APIError

        try:
            order = self._c().close_position(symbol.upper())
        except APIError:  # 404 — no open position
            return None
        return str(getattr(order, "id", "")) or None

    def flatten_all(self) -> list[str]:
        """Market-close every open position (and cancel resting orders); return the order ids."""
        out: list[str] = []
        for resp in self._c().close_all_positions(cancel_orders=True):
            # each entry carries the symbol + the submitted close order (under .body / .order)
            body = getattr(resp, "body", None) or getattr(resp, "order", None)
            oid = getattr(body, "id", None) if body is not None else None
            if oid is not None:
                out.append(str(oid))
        return out

    def cancel(self, order_id: str) -> bool:
        """Cancel a resting Alpaca order by id."""
        from alpaca.common.exceptions import APIError

        try:
            self._c().cancel_order_by_id(str(order_id))
            return True
        except APIError:
            return False

    def list_orders(self, limit: int = 50) -> list[dict]:
        """Recent Alpaca orders mapped to the OFT PaperOrder shape (UUID string ids)."""
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._c().get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit)
        )
        return [self._map_order(o) for o in orders]

    @staticmethod
    def _map_order(o: Any) -> dict:
        def _f(v: Any) -> float | None:
            return None if v in (None, "") else float(v)

        status = str(getattr(o.status, "value", o.status)).lower()
        submitted = getattr(o, "submitted_at", None) or getattr(o, "created_at", None)
        return {
            "id": str(o.id),
            "ts": submitted.isoformat() if submitted is not None else "",
            "symbol": o.symbol,
            "asset": "equity",
            "side": str(getattr(o.side, "value", o.side)).lower(),
            "quantity": _f(o.qty) or 0.0,
            "type": str(getattr(o.type, "value", o.type)).lower(),
            "limit_price": _f(getattr(o, "limit_price", None)),
            "status": "filled" if status == "filled" else ("open" if status in _OPEN_STATUSES else "cancelled"),
            "fill_price": _f(getattr(o, "filled_avg_price", None)),
            "broker_order_id": str(o.id),
            "stop_price": _f(getattr(o, "stop_price", None)),
            "trail_pct": _f(getattr(o, "trail_percent", None)),
        }
