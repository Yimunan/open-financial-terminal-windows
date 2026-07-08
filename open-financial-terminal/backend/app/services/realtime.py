"""Realtime market-data hub: ccxt.pro exchange websockets fanned out to UI subscribers.

Design:

* Topics are strings — ``ticker:{exchange}:{symbol}``, ``book:{exchange}:{symbol}``,
  ``trades:{exchange}:{symbol}`` (e.g. ``book:binance:BTC/USDT``).
* One asyncio watch-task per topic, ref-counted: started on the first subscriber,
  cancelled when the last unsubscribes. Exchange clients are shared per exchange id.
* Server-side coalescing: watch-tasks only update per-topic buffers; a single flusher
  task pushes dirty topics to subscriber queues every ``FLUSH_INTERVAL`` seconds.
  Ticker/book buffers are snapshot-replaced (latest wins); trades are append-buffered
  and drained on flush, so no prints are lost.
* Subscribers are ``asyncio.Queue``s owned by websocket connections. Slow consumers
  drop frames (``put_nowait`` on a bounded queue) instead of back-pressuring the hub.

Crypto has real ticker/book/trades via ccxt.pro. Equities have ticker + trades via Alpaca (no L2
depth). Order-book depth for equities, rates, FX and commodities comes from a pluggable depth source
(see ``services/depth.py``): a ``book:<source>.<asset>:<symbol>`` topic is routed to that producer,
whose snapshots flow out through the same flusher. Crypto's real book (``book:<exchange>:<symbol>``,
no dot) is unchanged.

The time-&-sales tape follows the same shape: crypto (ccxt.pro) and equity (Alpaca) keep their real
plain-token trades path, while rates/FX/commodity — with no free print feed — get a pluggable trades
source (see ``services/trades.py``) via a ``trades:<source>.<asset>:<symbol>`` topic, whose prints
flow out through the same flusher.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("oft.realtime")

FLUSH_INTERVAL = 0.15      # seconds between fan-out flushes (UI render cadence)
BOOK_DEPTH = 25            # price levels per side sent to the UI
TRADE_BUFFER_MAX = 200     # safety cap on buffered prints between flushes
RETRY_DELAY = 3.0          # seconds before restarting a failed exchange watch loop

KINDS = ("ticker", "book", "trades")

# Sentinel "exchange" token for equity topics (e.g. ticker:alpaca:AAPL). Routed to the Alpaca
# stream manager instead of ccxt.pro. Alpaca gives ticker + trades (NBBO + tape) but no L2 depth —
# equity/FICC order-book depth is served instead by the pluggable depth source below.
EQUITY_SOURCE = "alpaca"

# A `book` topic whose "exchange" segment carries a "." (e.g. book:sim.equity:AAPL) is routed to a
# pluggable order-book depth source (services/depth.py), NOT ccxt.pro. The segment is <source>.<asset>
# so the producer knows which mid provider + symbol mapping to use. Crypto's real ccxt.pro book keeps
# a plain exchange id (book:kraken:BTC/USDT, no dot) and its original watch path.
def _is_depth_book(kind: str, exchange: str) -> bool:
    return kind == "book" and "." in exchange


# A `trades` topic whose "exchange" segment carries a "." (e.g. trades:sim.rates:ZN) is routed to a
# pluggable trades source (services/trades.py), NOT ccxt.pro/Alpaca. The segment is <source>.<asset>
# so the producer knows which mid provider + symbol mapping to use. Crypto's real ccxt.pro tape keeps
# a plain exchange id (trades:kraken:BTC/USDT) and equity's Alpaca tape keeps trades:alpaca:AAPL — no
# dot in either, so both stay on their original watch paths.
def _is_sourced_trades(kind: str, exchange: str) -> bool:
    return kind == "trades" and "." in exchange


@dataclass
class _Topic:
    key: str
    kind: str
    exchange: str
    symbol: str
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    snapshot: dict | None = None          # latest ticker/book payload (replaced)
    trades: list[dict] = field(default_factory=list)  # buffered prints (drained)
    dirty: bool = False


def parse_topic(key: str) -> tuple[str, str, str]:
    """``kind:exchange:symbol`` → parts. Symbol may contain ':' only never for spot pairs."""
    parts = key.split(":", 2)
    if len(parts) != 3 or parts[0] not in KINDS or not parts[1] or not parts[2]:
        raise ValueError(f"bad topic '{key}' (want kind:exchange:symbol, kind in {KINDS})")
    return parts[0], parts[1].lower(), parts[2].upper()


class RealtimeHub:
    def __init__(self) -> None:
        self._topics: dict[str, _Topic] = {}
        self._exchanges: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task | None = None
        self._equity: Any = None          # lazily-built AlpacaStreamManager (equity topics)
        self._equity_reset = False        # set by request_equity_reset(); honored in the flush loop
        self._depth: dict[str, Any] = {}  # source token → lazily-built DepthSource (depth topics)
        self._depth_reset = False         # set by request_depth_reset(); honored in the flush loop
        self._trades: dict[str, Any] = {} # source token → lazily-built TradesSource (sourced tape)

    def _equity_mgr(self) -> Any:
        """The shared Alpaca stream manager (built on first equity topic)."""
        if self._equity is None:
            from app.services.equity_stream import AlpacaStreamManager

            self._equity = AlpacaStreamManager()
        return self._equity

    def _depth_mgr(self, source: str) -> Any:
        """The shared depth producer for a source token (built on first depth topic of that source)."""
        mgr = self._depth.get(source)
        if mgr is None:
            from app.services.depth import build_depth_source

            mgr = build_depth_source(source)
            if mgr is not None:
                self._depth[source] = mgr
        return mgr

    def _trades_mgr(self, source: str) -> Any:
        """The shared trades producer for a source token (built on first sourced-trades topic)."""
        mgr = self._trades.get(source)
        if mgr is None:
            from app.services.trades import build_trades_source

            mgr = build_trades_source(source)
            if mgr is not None:
                self._trades[source] = mgr
        return mgr

    def request_equity_reset(self) -> None:
        """Flag the Alpaca stream to rebuild (new creds/feed) on the next flush tick.

        Sync + cheap so it can be called from the settings PUT handler; the actual async teardown
        happens in the event loop (``_flush_loop``).
        """
        self._equity_reset = True

    def request_depth_reset(self) -> None:
        """Flag the depth producers to rebuild (new source/creds) on the next flush tick.

        Sync + cheap (called from the settings PUT handler); the async ``reset()`` runs in the loop.
        """
        self._depth_reset = True

    # ── subscription lifecycle ─────────────────────────────────────────────────
    async def subscribe(self, key: str, queue: asyncio.Queue) -> None:
        kind, exchange, symbol = parse_topic(key)
        key = f"{kind}:{exchange}:{symbol}"
        async with self._lock:
            topic = self._topics.get(key)
            if topic is None:
                topic = _Topic(key=key, kind=kind, exchange=exchange, symbol=symbol)
                self._topics[key] = topic
                if _is_depth_book(kind, exchange):
                    watch = self._watch_depth
                elif _is_sourced_trades(kind, exchange):
                    watch = self._watch_trades_source
                elif exchange == EQUITY_SOURCE:
                    watch = self._watch_equity
                else:
                    watch = self._watch
                topic.task = asyncio.create_task(watch(topic), name=f"watch-{key}")
            topic.subscribers.add(queue)
            if self._flusher is None or self._flusher.done():
                self._flusher = asyncio.create_task(self._flush_loop(), name="rt-flusher")
        # replay the latest snapshot so a fresh widget paints immediately
        if topic.snapshot is not None:
            _offer(queue, {"topic": key, "type": kind, "data": topic.snapshot})

    def _release_topic(self, key: str, topic: _Topic) -> None:
        """Tear down a topic with no subscribers left (cancel task; drop Alpaca sub for equities)."""
        if topic.task:
            topic.task.cancel()
        if topic.exchange == EQUITY_SOURCE and self._equity is not None:
            self._equity.unsubscribe(topic.kind, topic.symbol)
        elif _is_depth_book(topic.kind, topic.exchange):
            source, _, asset = topic.exchange.partition(".")
            mgr = self._depth.get(source)
            if mgr is not None:
                mgr.unsubscribe(asset, topic.symbol)
        elif _is_sourced_trades(topic.kind, topic.exchange):
            source, _, asset = topic.exchange.partition(".")
            mgr = self._trades.get(source)
            if mgr is not None:
                mgr.unsubscribe(asset, topic.symbol)
        del self._topics[key]

    async def unsubscribe(self, key: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            topic = self._topics.get(key)
            if topic is None:
                return
            topic.subscribers.discard(queue)
            if not topic.subscribers:
                self._release_topic(key, topic)

    async def drop_queue(self, queue: asyncio.Queue) -> None:
        """Remove a disconnected websocket's queue from every topic."""
        async with self._lock:
            for key in list(self._topics):
                topic = self._topics[key]
                topic.subscribers.discard(queue)
                if not topic.subscribers:
                    self._release_topic(key, topic)

    async def close(self) -> None:
        async with self._lock:
            for topic in self._topics.values():
                if topic.task:
                    topic.task.cancel()
            self._topics.clear()
            if self._flusher:
                self._flusher.cancel()
            for ex in self._exchanges.values():
                try:
                    await ex.close()
                except Exception:  # noqa: BLE001 - best-effort shutdown
                    pass
            self._exchanges.clear()
            if self._equity is not None:
                await self._equity.close()
            for mgr in (*self._depth.values(), *self._trades.values()):
                try:
                    await mgr.close()
                except Exception:  # noqa: BLE001 - best-effort shutdown
                    pass
            self._depth.clear()
            self._trades.clear()

    def stats(self) -> dict:
        exchanges = sorted(self._exchanges)
        if self._equity is not None and self._equity.enabled():
            exchanges = sorted({*exchanges, EQUITY_SOURCE})
        if self._depth or self._trades:
            exchanges = sorted({*exchanges, *self._depth, *self._trades})
        return {
            "topics": sorted(self._topics),
            "subscribers": {k: len(t.subscribers) for k, t in self._topics.items()},
            "exchanges": exchanges,
        }

    # ── upstream watch loops ───────────────────────────────────────────────────
    async def _get_exchange(self, exchange_id: str) -> Any:
        ex = self._exchanges.get(exchange_id)
        if ex is None:
            import ccxt.pro as ccxtpro

            klass = getattr(ccxtpro, exchange_id, None)
            if klass is None:
                raise ValueError(f"unknown exchange '{exchange_id}'")
            ex = klass({"enableRateLimit": True, "newUpdates": True})
            self._exchanges[exchange_id] = ex
        return ex

    async def _watch_equity(self, topic: _Topic) -> None:
        """Bridge an equity topic to the Alpaca stream manager (which pushes into the buffers).

        Unlike the ccxt loops, there is no polling here — the manager invokes our sink on each
        trade/quote. The task just registers the sink and parks until cancelled (on last unsubscribe).
        """
        mgr = self._equity_mgr()
        if topic.kind == "ticker":
            def sink(payload: dict, _t: _Topic = topic) -> None:
                _t.snapshot = payload
                _t.dirty = True
        else:  # trades (the time-&-sales tape); book has no equity source → stays empty
            def sink(payload: dict, _t: _Topic = topic) -> None:
                _t.trades.append(payload)
                if len(_t.trades) > TRADE_BUFFER_MAX:
                    del _t.trades[:-TRADE_BUFFER_MAX]
                _t.dirty = True
        try:
            mgr.subscribe(topic.kind, topic.symbol, sink)
            if not mgr.enabled():
                self._broadcast_now(topic, {"topic": topic.key, "type": "status",
                                            "data": {"state": "unavailable",
                                                     "error": "Alpaca credentials not configured"}})
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return

    async def _watch_depth(self, topic: _Topic) -> None:
        """Bridge a ``book:<source>.<asset>:<symbol>`` topic to its pluggable depth producer.

        Like ``_watch_equity``, there is no polling here — the producer pushes book snapshots into
        our sink (fanned out by the existing flusher) and status frames straight to subscribers. The
        task just registers the sinks and parks until cancelled (on the last unsubscribe).
        """
        source, _, asset = topic.exchange.partition(".")
        mgr = self._depth_mgr(source)

        def on_book(frame: dict, _t: _Topic = topic) -> None:
            _t.snapshot = frame
            _t.dirty = True

        def on_status(st: dict, _t: _Topic = topic) -> None:
            self._broadcast_now(_t, {"topic": _t.key, "type": "status", "data": st})

        try:
            if mgr is None or not mgr.enabled(asset):
                on_status({"state": "unavailable", "error": f"depth source '{source}' unavailable"})
            if mgr is not None:
                mgr.subscribe(asset, topic.symbol, on_book, on_status)
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return

    async def _watch_trades_source(self, topic: _Topic) -> None:
        """Bridge a ``trades:<source>.<asset>:<symbol>`` topic to its pluggable trades producer.

        Like ``_watch_depth``, there is no polling here — the producer pushes prints into our sink
        (append-buffered + drained by the flusher, so none are lost) and status frames straight to
        subscribers. The task registers the sinks and parks until cancelled (on the last unsubscribe).
        """
        source, _, asset = topic.exchange.partition(".")
        mgr = self._trades_mgr(source)

        def on_trade(print_: dict, _t: _Topic = topic) -> None:
            _t.trades.append(print_)
            if len(_t.trades) > TRADE_BUFFER_MAX:
                del _t.trades[:-TRADE_BUFFER_MAX]
            _t.dirty = True

        def on_status(st: dict, _t: _Topic = topic) -> None:
            self._broadcast_now(_t, {"topic": _t.key, "type": "status", "data": st})

        try:
            if mgr is None or not mgr.enabled(asset):
                on_status({"state": "unavailable", "error": f"trades source '{source}' unavailable"})
            if mgr is not None:
                mgr.subscribe(asset, topic.symbol, on_trade, on_status)
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return

    async def _watch(self, topic: _Topic) -> None:
        while True:
            try:
                ex = await self._get_exchange(topic.exchange)
                if topic.kind == "ticker":
                    await self._watch_ticker(ex, topic)
                elif topic.kind == "book":
                    await self._watch_book(ex, topic)
                else:
                    await self._watch_trades(ex, topic)
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001 - upstream hiccups: notify + retry
                log.warning("watch %s failed: %s — retrying in %.0fs", topic.key, e, RETRY_DELAY)
                topic.snapshot = None
                self._broadcast_now(topic, {"topic": topic.key, "type": "status",
                                            "data": {"state": "reconnecting", "error": str(e)}})
                await asyncio.sleep(RETRY_DELAY)

    async def _watch_ticker(self, ex: Any, topic: _Topic) -> None:
        while True:
            t = await ex.watch_ticker(topic.symbol)
            topic.snapshot = {
                "last": t.get("last"),
                "bid": t.get("bid"),
                "ask": t.get("ask"),
                "bid_size": t.get("bidVolume"),
                "ask_size": t.get("askVolume"),
                "change_pct": t.get("percentage"),
                "base_volume": t.get("baseVolume"),
                "ts": t.get("timestamp"),
            }
            topic.dirty = True

    async def _watch_book(self, ex: Any, topic: _Topic) -> None:
        while True:
            ob = await ex.watch_order_book(topic.symbol)
            topic.snapshot = {
                "bids": [[float(p), float(s)] for p, s in ob["bids"][:BOOK_DEPTH]],
                "asks": [[float(p), float(s)] for p, s in ob["asks"][:BOOK_DEPTH]],
                "ts": ob.get("timestamp"),
            }
            topic.dirty = True

    async def _watch_trades(self, ex: Any, topic: _Topic) -> None:
        while True:
            trades = await ex.watch_trades(topic.symbol)
            for t in trades:
                topic.trades.append({
                    "price": t.get("price"),
                    "amount": t.get("amount"),
                    "side": t.get("side"),
                    "ts": t.get("timestamp"),
                })
            if len(topic.trades) > TRADE_BUFFER_MAX:
                del topic.trades[:-TRADE_BUFFER_MAX]
            topic.dirty = True

    # ── fan-out ────────────────────────────────────────────────────────────────
    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            if self._equity_reset:
                self._equity_reset = False
                if self._equity is not None:
                    try:
                        await self._equity.reset()
                    except Exception as e:  # noqa: BLE001 - reset is best-effort
                        log.warning("equity stream reset failed: %s", e)
            if self._depth_reset:
                self._depth_reset = False
                for src, mgr in list(self._depth.items()):
                    try:
                        await mgr.reset()
                    except Exception as e:  # noqa: BLE001 - reset is best-effort
                        log.warning("depth source '%s' reset failed: %s", src, e)
            for topic in list(self._topics.values()):
                if not topic.dirty:
                    continue
                topic.dirty = False
                if topic.kind == "trades":
                    data, topic.trades = topic.trades, []
                else:
                    data = topic.snapshot
                if data is None or data == []:
                    continue
                self._broadcast_now(topic, {"topic": topic.key, "type": topic.kind, "data": data})

    def _broadcast_now(self, topic: _Topic, message: dict) -> None:
        for queue in topic.subscribers:
            _offer(queue, message)


def _offer(queue: asyncio.Queue, message: dict) -> None:
    """Non-blocking enqueue: a slow consumer loses frames, never stalls the hub."""
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        pass


_hub: RealtimeHub | None = None


def get_hub() -> RealtimeHub:
    global _hub
    if _hub is None:
        _hub = RealtimeHub()
    return _hub
