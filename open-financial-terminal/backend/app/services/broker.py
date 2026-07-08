"""Local paper-trading simulator implementing qhfi's Broker protocol.

State (cash, positions, orders) lives in the terminal SQLite so it survives restarts.
Market orders fill immediately at the symbol's last close; limit orders fill if currently
marketable, else rest as `open` and are re-checked against the latest price on each read.
This is the no-keys default; `deps.get_broker()` swaps in qhfi's real AlpacaPaperBroker when
Alpaca keys are configured. Both satisfy the same `Broker` protocol so the router is identical.
"""

from __future__ import annotations

import logging

from qhfi.data.manager import DataManager
from qhfi.execution.base import Account, Order, OrderSide, Position

from app.services.market import fetch_bars, quote_from_bars
from app.store import TerminalStore

log = logging.getLogger("oft.paper.sim")


class SimBroker:
    """In-process paper broker; same surface as qhfi.execution.base.Broker plus sim-only ops."""

    def __init__(
        self,
        store: TerminalStore,
        dm: DataManager,
        initial_cash: float,
        commission_bps: float = 0.0,
        slippage_bps: float = 0.0,
        account_id: int = 1,
    ):
        self.store = store
        self.dm = dm
        self.initial_cash = initial_cash
        self.comm = commission_bps / 10_000.0
        self.slip = slippage_bps / 10_000.0  # adverse price impact applied to market fills
        self.account_id = account_id  # which sim book this broker addresses (1 = default)
        self._asset: dict[str, str] = {}  # remember asset class per symbol for pricing
        # Seed the account row at initial_cash on first use; a no-op once the book exists, so live
        # cash/realized survive broker rebuilds (e.g. after a realism-config change clears the cache).
        store.ensure_paper_account(
            account_id, "Default" if account_id == 1 else f"Account {account_id}",
            initial_cash, commission_bps, slippage_bps,
        )

    # ── pricing ────────────────────────────────────────────────────────────────
    def last_price(self, symbol: str, asset: str = "equity") -> float | None:
        if asset == "option":
            # Options are priced off the chain service (per-contract $ = mark × 100), not qhfi bars.
            from app.services.options import option_mark

            return option_mark(symbol)
        try:
            _, bars = fetch_bars(self.dm, symbol, asset)
        except Exception:  # noqa: BLE001 - unknown/halted symbol
            return None
        q = quote_from_bars(bars)
        return q.get("price")

    # ── Broker protocol ──────────────────────────────────────────────────────────
    def get_positions(self) -> dict[str, Position]:
        self._fill_pending()
        out: dict[str, Position] = {}
        for p in self.store.paper_positions(self.account_id):
            self._asset[p["symbol"]] = p["asset"]
            out[p["symbol"]] = Position(
                instrument_id=p["symbol"], quantity=p["quantity"], avg_price=p["avg_price"]
            )
        return out

    def get_account(self) -> Account:
        positions = self.get_positions()
        cash = self.store.paper_cash(self.account_id)
        mkt = 0.0
        for sym, pos in positions.items():
            px = self.last_price(sym, self._asset.get(sym, "equity")) or pos.avg_price
            mkt += pos.quantity * px
        return Account(equity=cash + mkt, cash=cash, positions=positions)

    ORDER_TYPES = ("market", "limit", "stop", "stop_limit", "trailing_stop")

    def submit(
        self,
        order: Order,
        asset: str = "equity",
        stop_price: float | None = None,
        trail_pct: float | None = None,
    ) -> str:
        symbol = order.instrument_id.upper()
        self._asset[symbol] = asset
        otype = order.type
        if otype not in self.ORDER_TYPES:
            raise ValueError(f"unsupported order type: {otype}")
        px = self.last_price(symbol, asset)
        if px is None:
            raise ValueError(f"no price available for {symbol}")
        side = order.side

        # ── immediate-fill types ──
        if otype == "market":
            fill_px = self._slipped(px, side)
            self._guard_buying_power(side, order.quantity, fill_px)
            return self._record_fill(symbol, asset, side, order.quantity, otype, order.limit_price, fill_px)
        if otype == "limit":
            if order.limit_price is None:
                raise ValueError("limit order needs a limit_price")
            if self._limit_marketable(side, px, order.limit_price):
                self._guard_buying_power(side, order.quantity, order.limit_price)
                return self._record_fill(symbol, asset, side, order.quantity, otype, order.limit_price, order.limit_price)
            return self._record_open(symbol, asset, side, order.quantity, otype, limit_price=order.limit_price)

        # ── stop family (rests until the price crosses the stop) ──
        if otype == "trailing_stop":
            if not trail_pct or trail_pct <= 0:
                raise ValueError("trailing_stop needs trail_pct > 0")
            hwm = px
            stop_price = self._trail_stop(side, hwm, trail_pct)
        else:  # stop / stop_limit
            if stop_price is None:
                raise ValueError(f"{otype} needs a stop_price")
            hwm = None
            if otype == "stop_limit" and order.limit_price is None:
                raise ValueError("stop_limit needs a limit_price")
        # already through the stop at submit → trigger now (fill or, for stop_limit, drop to a limit)
        if self._stop_triggered(side, px, stop_price):
            if otype == "stop_limit" and not self._limit_marketable(side, px, order.limit_price):
                return self._record_open(symbol, asset, side, order.quantity, "limit", limit_price=order.limit_price)
            fill_px = order.limit_price if otype == "stop_limit" else self._slipped(px, side)
            return self._record_fill(symbol, asset, side, order.quantity, otype, order.limit_price, fill_px)
        return self._record_open(
            symbol, asset, side, order.quantity, otype,
            limit_price=order.limit_price, stop_price=stop_price, trail_pct=trail_pct, hwm=hwm,
        )

    # ── buying power & quick actions ──────────────────────────────────────────────
    def buying_power(self) -> float:
        return self.store.paper_cash(self.account_id)

    def close_position(self, symbol: str) -> str | None:
        """Market-close the whole position in ``symbol`` (sell a long / buy back a short)."""
        symbol = symbol.upper()
        pos = next((p for p in self.store.paper_positions(self.account_id) if p["symbol"] == symbol), None)
        if pos is None or abs(pos["quantity"]) < 1e-9:
            return None
        asset = pos["asset"]
        side = OrderSide.SELL if pos["quantity"] > 0 else OrderSide.BUY
        order = Order(instrument_id=symbol, side=side, quantity=abs(pos["quantity"]), type="market")
        return self.submit(order, asset)

    def flatten_all(self) -> list[str]:
        """Market-close every open position; returns the submitted order ids."""
        ids: list[str] = []
        for p in list(self.store.paper_positions(self.account_id)):
            oid = self.close_position(p["symbol"])
            if oid is not None:
                ids.append(oid)
        return ids

    # ── sim-only operations ──────────────────────────────────────────────────────
    def list_orders(self, limit: int = 50) -> list[dict]:
        self._fill_pending()
        return self.store.list_paper_orders(limit, self.account_id)

    def cancel(self, order_id: int | str) -> bool:
        try:
            oid = int(order_id)  # route passes a string; sim rows use int autoincrement ids
        except (TypeError, ValueError):
            return False
        for o in self.store.open_paper_orders(self.account_id):
            if o["id"] == oid:
                self.store.update_paper_order(oid, "cancelled", None)
                return True
        return False

    def reset(self) -> None:
        self.store.reset_paper(self.initial_cash, self.account_id)

    # ── pricing / trigger helpers ─────────────────────────────────────────────────
    def _slipped(self, px: float, side: OrderSide) -> float:
        """Adverse market fill: buys cross up, sells cross down by the configured slippage."""
        sign = 1.0 if side == OrderSide.BUY else -1.0
        return px * (1.0 + sign * self.slip)

    @staticmethod
    def _limit_marketable(side: OrderSide, px: float, limit: float | None) -> bool:
        return limit is not None and (
            (side == OrderSide.BUY and px <= limit) or (side == OrderSide.SELL and px >= limit)
        )

    @staticmethod
    def _stop_triggered(side: OrderSide, px: float, stop: float | None) -> bool:
        """A sell stop trips when price falls to/through it; a buy stop when price rises to/through it."""
        if stop is None:
            return False
        return (side == OrderSide.SELL and px <= stop) or (side == OrderSide.BUY and px >= stop)

    @staticmethod
    def _trail_stop(side: OrderSide, ref: float, trail_pct: float) -> float:
        """Stop level a trail_pct away from the favorable extreme (below a long high / above a short low)."""
        f = trail_pct / 100.0
        return ref * (1.0 - f) if side == OrderSide.SELL else ref * (1.0 + f)

    def _advance_trail(self, side: OrderSide, px: float, hwm: float, trail_pct: float) -> tuple[float, float]:
        """Ratchet the high/low-water mark toward favorable and recompute the stop (never loosens)."""
        ext = max(hwm, px) if side == OrderSide.SELL else min(hwm, px)  # hwm field = high(sell)/low(buy)
        return ext, self._trail_stop(side, ext, trail_pct)

    def _guard_buying_power(self, side: OrderSide, qty: float, price: float) -> None:
        """Block a long that would spend more cash than is available (no margin on the buy side)."""
        if side != OrderSide.BUY:
            return  # shorts are treated as cash-secured here; no buy-side leverage modeled
        cost = qty * price * (1.0 + self.comm)
        cash = self.store.paper_cash(self.account_id)
        if cost > cash + 1e-6:
            log.info(
                "acct=%s reject BUY %s qty=%s @ %.4f: insufficient buying power (need %.2f, have %.2f)",
                self.account_id, "", qty, price, cost, cash,
            )
            raise ValueError(f"insufficient buying power: need ${cost:,.2f}, have ${cash:,.2f}")

    def _record_fill(self, symbol, asset, side, qty, otype, limit_price, fill_px) -> str:
        realized = self._apply_fill(symbol, asset, side, qty, fill_px)
        oid = self.store.add_paper_order({
            "symbol": symbol, "asset": asset, "side": side.value, "quantity": qty,
            "type": otype, "limit_price": limit_price, "status": "filled", "fill_price": round(fill_px, 6),
        }, self.account_id)
        if realized:
            self.store.add_paper_realized(oid, round(realized, 6), self.account_id)
        log.info(
            "acct=%s fill %s %s %s @ %.4f (type=%s realized=%.2f)",
            self.account_id, side.value, qty, symbol, fill_px, otype, realized,
        )
        return str(oid)

    def _record_open(self, symbol, asset, side, qty, otype, *, limit_price=None,
                     stop_price=None, trail_pct=None, hwm=None) -> str:
        oid = self.store.add_paper_order({
            "symbol": symbol, "asset": asset, "side": side.value, "quantity": qty,
            "type": otype, "limit_price": limit_price, "status": "open", "fill_price": None,
            "stop_price": stop_price, "trail_pct": trail_pct, "hwm": hwm,
        }, self.account_id)
        return str(oid)

    def _fill_open_at(self, o: dict, side: OrderSide, fill_px: float) -> None:
        realized = self._apply_fill(o["symbol"], o["asset"], side, o["quantity"], fill_px)
        self.store.update_paper_order(o["id"], "filled", round(fill_px, 6))
        if realized:
            self.store.add_paper_realized(o["id"], round(realized, 6), self.account_id)
        log.info(
            "acct=%s fill(resting) %s %s %s @ %.4f (type=%s realized=%.2f)",
            self.account_id, side.value, o["quantity"], o["symbol"], fill_px, o["type"], realized,
        )

    # ── internals ────────────────────────────────────────────────────────────────
    def _apply_fill(self, symbol: str, asset: str, side: OrderSide, qty: float, price: float) -> float:
        """Apply a fill to cash + position; return realized P&L booked by this fill (0 if none).

        Realized P&L is booked whenever the fill reduces/closes/flips the existing position:
        the closed quantity is marked out from its average cost. It is gross of commission
        (commission is already reflected in cash/equity separately).
        """
        cash = self.store.paper_cash(self.account_id)
        pos = next((p for p in self.store.paper_positions(self.account_id) if p["symbol"] == symbol), None)
        cur_qty = pos["quantity"] if pos else 0.0
        cur_avg = pos["avg_price"] if pos else 0.0
        signed = qty if side == OrderSide.BUY else -qty
        commission = abs(qty) * price * self.comm
        cash -= signed * price + commission  # buying spends cash, selling adds it

        # realized P&L: only the portion that offsets an opposite-direction position
        realized = 0.0
        opposite = cur_qty != 0 and (cur_qty > 0) != (signed > 0)
        if opposite:
            closed = min(abs(signed), abs(cur_qty))
            # long closed by a sell → (exit - cost); short closed by a buy → (cost - exit)
            realized = closed * (price - cur_avg) * (1.0 if cur_qty > 0 else -1.0)

        new_qty = cur_qty + signed
        if cur_qty == 0 or (cur_qty > 0) == (signed > 0):
            # opening or adding in the same direction → weighted average price
            new_avg = (abs(cur_qty) * cur_avg + abs(signed) * price) / (abs(cur_qty) + abs(signed)) if new_qty else 0.0
        elif (new_qty > 0) == (cur_qty > 0) or new_qty == 0:
            new_avg = cur_avg  # reducing/closing → keep avg
        else:
            new_avg = price  # flipped through zero → new basis at fill
        self.store.set_paper_cash(cash, self.account_id)
        self.store.upsert_paper_position(symbol, asset, new_qty, new_avg, self.account_id)
        return realized

    def _fill_pending(self) -> None:
        """Re-check every resting order against the latest price and fill/advance as warranted.

        Limit → fills when marketable. Stop / trailing-stop → fills at market once triggered (a
        trailing stop first ratchets its level toward favorable). Stop-limit → on trigger fills at
        its limit if marketable, else drops to a resting limit. Called on each order/account read.
        """
        for o in self.store.open_paper_orders(self.account_id):
            px = self.last_price(o["symbol"], o["asset"])
            if px is None:
                continue
            side = OrderSide(o["side"])
            otype = o["type"]
            if otype == "limit":
                if self._limit_marketable(side, px, o["limit_price"]):
                    self._fill_open_at(o, side, o["limit_price"])
            elif otype == "stop":
                if self._stop_triggered(side, px, o["stop_price"]):
                    self._fill_open_at(o, side, self._slipped(px, side))
            elif otype == "trailing_stop":
                new_hwm, new_stop = self._advance_trail(side, px, o["hwm"], o["trail_pct"])
                if new_hwm != o["hwm"] or new_stop != o["stop_price"]:
                    self.store.update_paper_order_trail(o["id"], round(new_stop, 6), round(new_hwm, 6))
                if self._stop_triggered(side, px, new_stop):
                    self._fill_open_at(o, side, self._slipped(px, side))
            elif otype == "stop_limit":
                if self._stop_triggered(side, px, o["stop_price"]):
                    if self._limit_marketable(side, px, o["limit_price"]):
                        self._fill_open_at(o, side, o["limit_price"])
                    else:  # triggered but not marketable → becomes a resting limit
                        self.store.convert_paper_order_to_limit(o["id"], o["limit_price"])
