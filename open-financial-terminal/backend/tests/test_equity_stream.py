"""Offline tests for the Alpaca equity stream → realtime-hub bridge.

No socket is opened: `StockDataStream` is monkeypatched with a fake that records subscribe/unsubscribe
calls and exposes the registered handlers. We then feed fake Trade/Quote objects through the manager
handlers and assert (a) the payloads match the hub's ccxt-shaped dicts, (b) Alpaca subscriptions are
ref-counted across ticker/trades, and (c) the hub routes an equity topic through the manager into the
topic buffer.

Run: `cd backend && pytest tests/test_equity_stream.py -v`
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services import equity_stream as es
from app.services.realtime import EQUITY_SOURCE, RealtimeHub


class FakeStream:
    """Stand-in for alpaca's StockDataStream — records subs, never touches the network."""

    def __init__(self, *_a, **_k) -> None:
        self.trade_subs: set[str] = set()
        self.quote_subs: set[str] = set()
        self.trade_handler = None
        self.quote_handler = None

    def subscribe_trades(self, handler, *symbols):
        self.trade_handler = handler
        self.trade_subs.update(symbols)

    def subscribe_quotes(self, handler, *symbols):
        self.quote_handler = handler
        self.quote_subs.update(symbols)

    def unsubscribe_trades(self, *symbols):
        self.trade_subs.difference_update(symbols)

    def unsubscribe_quotes(self, *symbols):
        self.quote_subs.difference_update(symbols)

    async def _run_forever(self):
        await asyncio.Future()  # park until cancelled

    async def stop_ws(self):
        pass


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


_TS = datetime(2024, 1, 2, 15, 30, tzinfo=timezone.utc)


@pytest.fixture()
def fake_alpaca(monkeypatch):
    monkeypatch.setattr(es, "StockDataStream", FakeStream)
    monkeypatch.setattr(es, "get_alpaca_creds", lambda: ("key", "secret", True))
    monkeypatch.setattr(es, "get_equity_feed", lambda: "iex")


def test_handlers_map_to_hub_payloads(fake_alpaca):
    async def run():
        mgr = es.AlpacaStreamManager()
        mgr._prev_close["AAPL"] = 100.0  # so change_pct computes without a thread/data fetch
        ticks, trades = [], []
        mgr.subscribe("ticker", "AAPL", ticks.append)
        mgr.subscribe("trades", "AAPL", trades.append)
        await mgr._on_quote(Obj(symbol="AAPL", timestamp=_TS, bid_price=101.0, ask_price=101.5))
        await mgr._on_trade(Obj(symbol="AAPL", timestamp=_TS, price=102.0, size=10))
        await mgr.close()
        return ticks, trades

    ticks, trades = asyncio.run(run())
    snap = ticks[-1]
    assert snap["last"] == 102.0 and snap["bid"] == 101.0 and snap["ask"] == 101.5
    assert snap["change_pct"] == pytest.approx(2.0)  # (102-100)/100
    assert trades[-1] == {"price": 102.0, "amount": 10.0, "side": None, "ts": snap["ts"]}


def test_subscribe_refcounts_alpaca_channels(fake_alpaca):
    async def run():
        mgr = es.AlpacaStreamManager()

        def drain():
            # subscribe_*/unsubscribe_* are dispatched to a single-worker FIFO executor (the
            # deadlock fix in `_mutate`), so they land asynchronously. Submitting a sentinel and
            # blocking on it guarantees every queued mutation ahead of it has finished — a
            # deterministic barrier with no sleeps.
            mgr._mutator.submit(lambda: None).result()

        mgr.subscribe("ticker", "AAPL", lambda _p: None)  # ticker needs trades + quotes
        s = mgr._stream
        drain()
        assert "AAPL" in s.trade_subs and "AAPL" in s.quote_subs
        mgr.subscribe("trades", "AAPL", lambda _p: None)  # also wants trades
        mgr.unsubscribe("ticker", "AAPL")                 # quotes no longer needed; trades still are
        drain()
        assert "AAPL" not in s.quote_subs and "AAPL" in s.trade_subs
        mgr.unsubscribe("trades", "AAPL")                 # now nothing needs trades
        drain()
        assert "AAPL" not in s.trade_subs
        await mgr.close()

    asyncio.run(run())


def test_hub_routes_equity_topic_into_buffer(fake_alpaca):
    async def run():
        hub = RealtimeHub()
        key = f"ticker:{EQUITY_SOURCE}:AAPL"
        await hub.subscribe(key, asyncio.Queue())
        await asyncio.sleep(0)  # let the _watch_equity task register the sink
        mgr = hub._equity
        assert mgr is not None and mgr._stream is not None
        assert "AAPL" in mgr._stream.trade_subs and "AAPL" in mgr._stream.quote_subs
        mgr._prev_close["AAPL"] = 100.0
        await mgr._on_trade(Obj(symbol="AAPL", timestamp=_TS, price=110.0, size=5))
        topic = hub._topics[key]
        assert topic.dirty and topic.snapshot["last"] == 110.0
        await hub.close()

    asyncio.run(run())
