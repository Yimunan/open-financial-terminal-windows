"""Tests for the pluggable order-book depth source (services/depth.py) + hub routing.

Covers the pure book model, source construction, FICC symbol mapping in the mid resolver, and the
realtime hub routing that sends ``book:<source>.<asset>:<symbol>`` topics to a depth producer while
leaving crypto's real ccxt ``book:<exchange>:<symbol>`` path untouched.

Run: `cd backend && pytest tests/test_depth.py -v`
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import depth
from app.services.realtime import BOOK_DEPTH, RealtimeHub, _is_depth_book


# ── the synthetic book model ────────────────────────────────────────────────────────
def test_synthetic_book_frame_shape():
    fr = depth.synthetic_book_frame(100.0)
    assert len(fr["bids"]) == len(fr["asks"]) == BOOK_DEPTH
    sizes = [s for _, s in fr["bids"]] + [s for _, s in fr["asks"]]
    # regression: the ported model must keep ALL 25 levels strictly positive (the original
    # 1 - 0.1*lv decay went negative past level 10; the hub asks for 25 levels)
    assert all(s > 0 for s in sizes)
    # bids descend, asks ascend, mid sits between best bid/ask
    bids, asks = fr["bids"], fr["asks"]
    assert all(bids[i][0] > bids[i + 1][0] for i in range(len(bids) - 1))
    assert all(asks[i][0] < asks[i + 1][0] for i in range(len(asks) - 1))
    assert bids[0][0] < 100.0 < asks[0][0]
    assert isinstance(fr["ts"], int)


def test_synthetic_book_frame_spread_and_imbalance():
    fr = depth.synthetic_book_frame(200.0, spread_bps=10.0)
    spread_bps = (fr["asks"][0][0] - fr["bids"][0][0]) / 200.0 * 1e4
    assert spread_bps == pytest.approx(10.0, abs=0.01)
    # positive imbalance tilts bid sizes heavier than ask sizes
    tilted = depth.synthetic_book_frame(200.0, imbalance=0.5)
    assert tilted["bids"][0][1] > tilted["asks"][0][1]


# ── source construction ─────────────────────────────────────────────────────────────
def test_build_depth_source():
    assert isinstance(depth.build_depth_source("sim"), depth.SimulatedDepthSource)
    assert depth.build_depth_source("none") is None
    assert depth.build_depth_source("bogus") is None
    # Known vendor ids never crash the factory: it returns the vendor's manager when the (lazily
    # imported) provider module loads, or None if that optional module is unavailable — but never
    # raises. Actual usability (SDK + creds) is gated later by manager.enabled(asset).
    for vendor in ("ibkr", "databento", "dxfeed"):
        mgr = depth.build_depth_source(vendor)
        assert mgr is None or hasattr(mgr, "enabled")


# ── FICC symbol mapping in the mid resolver ──────────────────────────────────────────
@pytest.mark.parametrize("asset,symbol,expected_yf", [
    ("rates", "ZN", "ZN=F"),
    ("fx", "EUR/USD", "EURUSD=X"),
    ("commodity", "GC", "GC=F"),
    ("equity", "AAPL", "AAPL"),  # equity passes through unmapped
])
def test_latest_mid_maps_ficc_intraday_symbol(monkeypatch, asset, symbol, expected_yf):
    import pandas as pd

    captured = {}

    def fake_intraday(sym, a, tf):
        captured["sym"] = sym
        frame = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [42.0], "volume": [0]})
        return object(), frame

    monkeypatch.setattr("app.services.market.fetch_bars_intraday", fake_intraday)
    mid = depth.latest_mid(asset, symbol)
    assert mid == 42.0
    assert captured["sym"] == expected_yf   # intraday queried the yfinance ticker


def test_latest_mid_falls_back_to_daily_close(monkeypatch):
    import pandas as pd

    # intraday empty → must fall back to the DataManager daily close
    monkeypatch.setattr(
        "app.services.market.fetch_bars_intraday",
        lambda sym, a, tf: (object(), pd.DataFrame(columns=["open", "high", "low", "close", "volume"])),
    )
    monkeypatch.setattr("app.deps.get_data_manager", lambda: object())
    daily = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [77.0], "volume": [0]})
    monkeypatch.setattr("app.services.market.fetch_bars", lambda dm, symbol, asset: (object(), daily))
    assert depth.latest_mid("rates", "ZN") == 77.0


def test_latest_mid_none_when_no_price(monkeypatch):
    import pandas as pd

    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    monkeypatch.setattr("app.services.market.fetch_bars_intraday", lambda *a: (object(), empty))
    monkeypatch.setattr("app.deps.get_data_manager", lambda: object())
    monkeypatch.setattr("app.services.market.fetch_bars", lambda *a: (object(), empty))
    assert depth.latest_mid("equity", "ZZZZ") is None


# ── hub routing ──────────────────────────────────────────────────────────────────────
class _FakeDepthSource:
    """Records subscribe/unsubscribe so we can assert the hub routed + parsed the topic right."""

    def __init__(self):
        self.subs, self.unsubs = [], []

    def enabled(self, asset):
        return True

    def subscribe(self, asset, symbol, on_book, on_status):
        self.subs.append((asset, symbol))
        on_book(depth.synthetic_book_frame(100.0))  # push one snapshot immediately

    def unsubscribe(self, asset, symbol):
        self.unsubs.append((asset, symbol))

    async def reset(self):
        pass

    async def close(self):
        pass


def test_is_depth_book_predicate():
    assert _is_depth_book("book", "sim.equity") is True
    assert _is_depth_book("book", "kraken") is False        # crypto real L2 → not depth-routed
    assert _is_depth_book("ticker", "sim.equity") is False  # only book topics route to depth


def test_hub_routes_depth_topic_and_releases(monkeypatch):
    fake = _FakeDepthSource()
    monkeypatch.setattr(depth, "build_depth_source", lambda source: fake)

    async def scenario():
        hub = RealtimeHub()
        q: asyncio.Queue = asyncio.Queue()
        await hub.subscribe("book:sim.equity:AAPL", q)
        await asyncio.sleep(0.05)  # let the _watch_depth task register the sink
        # routed to the depth producer with the parsed (asset, symbol)
        assert fake.subs == [("equity", "AAPL")]
        # the pushed snapshot fans out through the normal flusher path → a book frame reaches the queue
        await asyncio.sleep(0.2)
        got = q.get_nowait()
        assert got["type"] == "book" and got["topic"] == "book:sim.equity:AAPL"
        # last unsubscribe tears the producer down
        await hub.unsubscribe("book:sim.equity:AAPL", q)
        assert fake.unsubs == [("equity", "AAPL")]
        await hub.close()

    asyncio.run(scenario())


def test_hub_leaves_crypto_book_on_ccxt_path(monkeypatch):
    # a plain exchange book topic must NOT hit the depth source (it uses the ccxt watch loop)
    fake = _FakeDepthSource()
    monkeypatch.setattr(depth, "build_depth_source", lambda source: fake)

    async def scenario():
        hub = RealtimeHub()
        q: asyncio.Queue = asyncio.Queue()
        # patch the ccxt watch loop to a no-op so we don't hit the network
        async def _noop(topic):
            while True:
                await asyncio.sleep(3600)

        monkeypatch.setattr(hub, "_watch", _noop)
        await hub.subscribe("book:kraken:BTC/USDT", q)
        await asyncio.sleep(0.05)
        assert fake.subs == []  # depth source untouched
        await hub.close()

    asyncio.run(scenario())
