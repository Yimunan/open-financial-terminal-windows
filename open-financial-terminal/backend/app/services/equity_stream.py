"""Alpaca equity real-time stream → realtime-hub bridge.

The realtime hub ([services/realtime.py]) is one-asyncio-task-per-topic. Alpaca's `StockDataStream`
is the opposite shape: ONE websocket connection that multiplexes many symbols, with per-symbol
trade/quote subscriptions added and removed dynamically. This manager owns that single connection and
bridges the two models:

* The hub registers a per-topic ``sink`` (via ``subscribe(kind, symbol, sink)``); the manager keeps
  the Alpaca subscriptions ref-counted and pushes converted payloads into the sink. The hub's existing
  flusher fans them out — no second fan-out path.
* ``ticker`` topics need both the last trade (price) and the NBBO quote (bid/ask), so the manager
  subscribes to trades *and* quotes and merges them into one snapshot per symbol. ``trades`` topics
  need only the trade stream (the time-&-sales tape).

Gated on Alpaca credentials: with no key the manager is inert (``enabled()`` is False) and equities
fall back to the existing polling. Feed (IEX free / SIP paid) comes from Settings → Market Data.

Honest limit: Alpaca IEX/SIP provide NBBO top-of-book + trades, not L2 depth — so there is no
order-book (`book`) path here; that stays crypto-only.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from alpaca.data.enums import DataFeed
from alpaca.data.live.stock import StockDataStream

from app.config import equity_realtime_enabled, get_alpaca_creds, get_equity_feed

log = logging.getLogger("oft.equity_stream")

RETRY_DELAY = 3.0  # seconds before restarting a dropped Alpaca stream connection

Sink = Callable[[dict], None]


def _ms(ts: Any) -> int | None:
    """Alpaca timestamps are datetimes → unix milliseconds (matching the ccxt payloads)."""
    try:
        return int(ts.timestamp() * 1000)
    except (AttributeError, TypeError, ValueError):
        return None


class AlpacaStreamManager:
    """Owns one `StockDataStream`; maps Alpaca trades/quotes onto per-topic hub sinks."""

    def __init__(self) -> None:
        self._stream: StockDataStream | None = None
        self._task: asyncio.Task | None = None
        # alpaca-py's subscribe_*/unsubscribe_* block on
        # ``run_coroutine_threadsafe(coro, self._loop).result()`` — safe only when called from a
        # thread *other* than the stream's loop. We run the stream on the app's main loop, so
        # calling them inline deadlocks it. A single-worker executor keeps that .result() wait off
        # the event loop and preserves sub/unsub ordering (FIFO).
        self._mutator = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alpaca-sub")
        # symbol → sink, per kind
        self._ticker_sinks: dict[str, Sink] = {}
        self._trades_sinks: dict[str, Sink] = {}
        # merged ticker snapshot per symbol (last trade + latest NBBO quote)
        self._ticker_state: dict[str, dict] = {}
        # which Alpaca channels are currently subscribed (dedup)
        self._sub_trades: set[str] = set()
        self._sub_quotes: set[str] = set()
        # prior daily close per symbol, for change_pct (best-effort, fetched off the event loop)
        self._prev_close: dict[str, float] = {}
        self._prev_close_pending: set[str] = set()

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def enabled(self) -> bool:
        """True when the equity realtime source is Alpaca AND credentials are configured.

        Setting Settings → Market Data → Equity realtime to 'Off' disables the stream even with
        creds present (equities then fall back to polling).
        """
        return equity_realtime_enabled()  # (auto-)resolved Alpaca source AND creds

    def _ensure_started(self) -> None:
        """Lazily build the stream + run task in the current event loop (no-op if no creds)."""
        if self._task is not None and not self._task.done():
            return
        key, secret, _ = get_alpaca_creds()
        if not key:
            return
        feed = DataFeed[get_equity_feed().upper()]
        self._stream = StockDataStream(key, secret, feed=feed)
        self._task = asyncio.create_task(self._run(), name="alpaca-stream")

    async def _run(self) -> None:
        """Drive the Alpaca websocket, reconnecting on upstream hiccups (subscriptions persist)."""
        assert self._stream is not None
        while True:
            try:
                await self._stream._run_forever()
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001 - upstream hiccup: log + retry
                log.warning("alpaca stream failed: %s — retrying in %.0fs", e, RETRY_DELAY)
                await asyncio.sleep(RETRY_DELAY)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._stream is not None:
            try:
                await self._stream.stop_ws()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                pass
        self._stream = None
        self._task = None
        self._sub_trades.clear()
        self._sub_quotes.clear()

    async def reset(self) -> None:
        """Tear down + rebuild against current creds/feed, re-issuing all live subscriptions."""
        await self.close()
        if not self.enabled():
            return  # creds removed → stay down; existing equity topics just go quiet
        self._ensure_started()
        for sym in list(self._ticker_sinks):
            self._ensure_trade_sub(sym)
            self._ensure_quote_sub(sym)
        for sym in list(self._trades_sinks):
            self._ensure_trade_sub(sym)

    # ── subscription management ──────────────────────────────────────────────────
    def subscribe(self, kind: str, symbol: str, sink: Sink) -> None:
        self._ensure_started()
        if self._stream is None:
            return
        if kind == "ticker":
            self._ticker_sinks[symbol] = sink
            self._ticker_state.setdefault(
                symbol,
                {"last": None, "bid": None, "ask": None, "bid_size": None,
                 "ask_size": None, "change_pct": None, "ts": None},
            )
            self._ensure_trade_sub(symbol)
            self._ensure_quote_sub(symbol)
        elif kind == "trades":
            self._trades_sinks[symbol] = sink
            self._ensure_trade_sub(symbol)

    def unsubscribe(self, kind: str, symbol: str) -> None:
        if kind == "ticker":
            self._ticker_sinks.pop(symbol, None)
            self._ticker_state.pop(symbol, None)
        elif kind == "trades":
            self._trades_sinks.pop(symbol, None)
        if self._stream is None:
            return
        # drop the Alpaca channels no symbol needs anymore
        if symbol not in self._ticker_sinks:
            if symbol in self._sub_quotes:
                self._sub_quotes.discard(symbol)
                self._mutate(self._stream.unsubscribe_quotes, symbol)
        if symbol not in self._ticker_sinks and symbol not in self._trades_sinks:
            if symbol in self._sub_trades:
                self._sub_trades.discard(symbol)
                self._mutate(self._stream.unsubscribe_trades, symbol)

    def _ensure_trade_sub(self, symbol: str) -> None:
        if self._stream is not None and symbol not in self._sub_trades:
            self._sub_trades.add(symbol)
            self._mutate(self._stream.subscribe_trades, self._on_trade, symbol)

    def _ensure_quote_sub(self, symbol: str) -> None:
        if self._stream is not None and symbol not in self._sub_quotes:
            self._sub_quotes.add(symbol)
            self._mutate(self._stream.subscribe_quotes, self._on_quote, symbol)

    def _mutate(self, fn: Callable, *args: Any) -> None:
        """Run an alpaca-py subscribe/unsubscribe off the event loop.

        These calls block on ``run_coroutine_threadsafe(coro, self._loop).result()``, which
        deadlocks when invoked on the very loop the stream runs on (our case). Submitting to the
        single-worker executor lets the .result() wait in a worker thread while the loop stays free
        to run the scheduled send coroutine. Best-effort: subscription sync errors are logged.
        """
        def _call() -> None:
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - subscription sync is best-effort
                log.exception("alpaca subscription update failed")

        self._mutator.submit(_call)

    # ── Alpaca handlers (async — invoked by the stream) ──────────────────────────
    async def _on_trade(self, trade: Any) -> None:
        sym = trade.symbol
        price = float(trade.price)
        ts = _ms(trade.timestamp)
        st = self._ticker_state.get(sym)
        if st is not None:
            st["last"] = price
            st["ts"] = ts
            st["change_pct"] = self._change_pct(sym, price)
            sink = self._ticker_sinks.get(sym)
            if sink:
                sink(dict(st))
        tsink = self._trades_sinks.get(sym)
        if tsink:
            tsink({"price": price, "amount": float(trade.size), "side": None, "ts": ts})

    async def _on_quote(self, quote: Any) -> None:
        sym = quote.symbol
        st = self._ticker_state.get(sym)
        if st is None:
            return
        st["bid"] = float(quote.bid_price)
        st["ask"] = float(quote.ask_price)
        # NBBO size-at-touch (best-bid / best-ask share counts); absent on some quotes → None
        bs = getattr(quote, "bid_size", None)
        as_ = getattr(quote, "ask_size", None)
        st["bid_size"] = float(bs) if bs is not None else None
        st["ask_size"] = float(as_) if as_ is not None else None
        st["ts"] = _ms(quote.timestamp)
        sink = self._ticker_sinks.get(sym)
        if sink:
            sink(dict(st))

    # ── change_pct from the prior daily close (best-effort, off the event loop) ──
    def _change_pct(self, symbol: str, last: float) -> float | None:
        prev = self._prev_close.get(symbol)
        if prev:
            return round((last - prev) / prev * 100, 4)
        if symbol not in self._prev_close_pending:
            self._prev_close_pending.add(symbol)
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, self._load_prev_close, symbol)
        return None

    def _load_prev_close(self, symbol: str) -> None:
        """Load the most recent cached daily close (sync; runs in a thread)."""
        try:
            from app.deps import get_data_manager, make_instrument

            dm = get_data_manager()
            inst = make_instrument(symbol, "equity")
            if dm.store.has(inst):
                bars = dm.store.load(inst)
                if not bars.empty:
                    self._prev_close[symbol] = float(bars["close"].iloc[-1])
        except Exception:  # noqa: BLE001 - change_pct is best-effort; leave it None
            pass
        finally:
            self._prev_close_pending.discard(symbol)
