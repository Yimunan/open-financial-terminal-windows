"""Pluggable order-book (L2 depth) sources → realtime-hub bridge.

The realtime hub ([services/realtime.py]) streams a live order book only for crypto (real ccxt.pro
`watch_order_book`). Equities, rates, FX and commodities have no market-wide L2 feed available for
free — spot FX has no central book at all, and equity/futures depth is a paid, entitlement-gated
vendor product. So this module introduces a **vendor-agnostic depth-source seam**: the hub routes a
``book:<source>.<asset>:<symbol>`` topic here, and a ``DepthSource`` for that source produces
``BookFrame`` snapshots (``{bids, asks, ts}``) which flow out through the hub's existing flusher.

The one producer shipped today is :class:`SimulatedDepthSource` — a local feed (no credentials) that
models an L2 ladder around the *real* mid (yfinance/ccxt), so the feature works end-to-end for all
four asset classes. It is honest about being synthetic (the widget tags it). A real vendor (IBKR,
Databento, …) plugs into the SAME seam later: implement :class:`DepthSource` in a sibling module and
add one branch to :func:`build_depth_source` — nothing else in the hub, settings, health or the
frontend changes.

Mirrors the bridge shape of ``services/equity_stream.AlpacaStreamManager`` (lazy start, per-topic
sink callbacks, ``reset()``/``close()``) and reuses the ladder math from
``services/market_making._synthetic_book``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Callable, Protocol

from app.services.realtime import BOOK_DEPTH, RETRY_DELAY

log = logging.getLogger("oft.depth")

#: A book snapshot in the on-the-wire ``BookFrame`` shape ``{bids, asks, ts}``.
BookSink = Callable[[dict], None]
#: A hub status frame, ``{"state": ..., "error": ...}`` (e.g. unavailable / reconnecting).
StatusSink = Callable[[dict], None]

# Simulated-feed model parameters (bps unless noted).
_DEFAULT_SPREAD_BPS = 4.0    # top-of-book spread as a fraction of mid
_DEFAULT_DEPTH = 5.0         # size at the best level (arbitrary units)


def synthetic_book_frame(
    mid: float, *, levels: int = BOOK_DEPTH, spread_bps: float = _DEFAULT_SPREAD_BPS,
    depth: float = _DEFAULT_DEPTH, imbalance: float = 0.0, ts: int | None = None,
) -> dict:
    """Model an L2 order book around a real ``mid`` → a ``BookFrame`` dict ``{bids, asks, ts}``.

    Adapted from ``market_making._synthetic_book`` to emit one live snapshot instead of a historical
    long-format frame. Bids descend / asks ascend from the mid; ``imbalance`` (−0.9..0.9) tilts the
    bid vs ask sizes to fake buy/sell pressure. The size taper is floored at 0.05 so all
    ``BOOK_DEPTH`` (25) levels stay strictly positive — the original ``1 − 0.1·lv`` went negative
    past level 10 (the MM backtest only ever asked for 5 levels, so it never surfaced there).
    """
    half = mid * spread_bps / 2.0 / 1e4
    bid_mult = 1.0 + imbalance
    ask_mult = 1.0 - imbalance
    bids, asks = [], []
    for lv in range(levels):
        step = half * (1 + 2 * lv)
        taper = max(0.05, 1.0 - 0.03 * lv)   # gentle size decay, floored so 25 levels stay > 0
        bids.append([round(mid - step, 8), round(depth * bid_mult * taper, 6)])
        asks.append([round(mid + step, 8), round(depth * ask_mult * taper, 6)])
    return {"bids": bids, "asks": asks, "ts": ts if ts is not None else int(time.time() * 1000)}


# FICC asset classes need their clean id mapped to the yfinance ticker for the *intraday* path
# (fetch_bars_intraday queries yfinance with instrument.id verbatim; the daily path maps it inside
# the provider). Reuse each provider's own _yf_symbol so the mapping never drifts.
_ficc_providers: dict[str, object] = {}


def _yf_intraday_symbol(asset: str, symbol: str) -> str:
    """Map a clean FICC id to its yfinance ticker (ZN→ZN=F, EUR/USD→EURUSD=X, GC→GC=F); else pass."""
    a = asset.lower()
    if a not in ("rates", "fx", "commodity"):
        return symbol
    prov = _ficc_providers.get(a)
    if prov is None:
        if a == "rates":
            from app.rates_provider import RatesFuturesProvider
            prov = RatesFuturesProvider()
        elif a == "fx":
            from app.fx_provider import FxProvider
            prov = FxProvider()
        else:
            from app.commodity_provider import CommodityFuturesProvider
            prov = CommodityFuturesProvider()
        _ficc_providers[a] = prov
    return prov._yf_symbol(symbol)  # type: ignore[attr-defined]


def latest_mid(asset: str, symbol: str) -> float | None:
    """A current-ish mid for any asset class, or ``None`` when no price is obtainable.

    Blocking (network) — call off the event loop. Tries the near-live intraday close first, then
    falls back to the DataManager's daily close (correct-by-construction for every class, incl.
    weekends / closed markets). Returns ``None`` only when both paths come up empty, so the caller
    can surface an honest "unavailable" instead of a fabricated book.
    """
    from app.services import market as mkt

    a = (asset or "equity").lower()
    # 1) intraday 1m close (near-live where available)
    try:
        sym = _yf_intraday_symbol(a, symbol)
        _, frame = mkt.fetch_bars_intraday(sym, a, "1m")
        if frame is not None and not frame.empty:
            v = float(frame["close"].iloc[-1])
            if v > 0:
                return v
    except Exception:  # noqa: BLE001 - fall through to the daily close
        pass
    # 2) daily close via the DataManager (maps + caches per class through the providers)
    try:
        from app.deps import get_data_manager

        _, bars = mkt.fetch_bars(get_data_manager(), symbol, a)
        if bars is not None and not bars.empty:
            v = float(bars["close"].iloc[-1])
            if v > 0:
                return v
    except Exception:  # noqa: BLE001 - no price obtainable
        pass
    return None


class DepthSource(Protocol):
    """A pluggable order-book producer. Poll (sim) or push (vendor) — both behind this interface."""

    def enabled(self, asset: str) -> bool: ...
    def subscribe(self, asset: str, symbol: str, on_book: BookSink, on_status: StatusSink) -> None: ...
    def unsubscribe(self, asset: str, symbol: str) -> None: ...
    async def reset(self) -> None: ...
    async def close(self) -> None: ...


class SimulatedDepthSource:
    """Local simulated depth: a modelled ladder around the real mid, refreshed on a timer.

    No credentials, always available. One asyncio task per ``(asset, symbol)`` recomputes a snapshot
    ~once a second; the mid is jittered by a tiny random walk so the book "breathes" and the depth
    chart animates, and the recent step's sign tilts the size imbalance. Honest about being
    synthetic — the widget tags it so it is never mistaken for real vendor L2.
    """

    TICK = 1.0                 # seconds between snapshots
    UNAVAILABLE_DELAY = 5.0    # backoff when no mid is obtainable (market closed / unknown symbol)
    JITTER_BPS = 3.0           # per-tick mid random-walk stdev (bps)
    IMBALANCE_GAIN = 0.15      # how strongly the random-walk step tilts bid/ask sizes

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def enabled(self, asset: str) -> bool:  # noqa: ARG002 - always available, no creds
        return True

    def subscribe(self, asset: str, symbol: str, on_book: BookSink, on_status: StatusSink) -> None:
        key = (asset, symbol)
        old = self._tasks.pop(key, None)
        if old is not None:
            old.cancel()
        self._tasks[key] = asyncio.create_task(
            self._loop(asset, symbol, on_book, on_status), name=f"sim-depth-{asset}:{symbol}",
        )

    def unsubscribe(self, asset: str, symbol: str) -> None:
        task = self._tasks.pop((asset, symbol), None)
        if task is not None:
            task.cancel()

    async def reset(self) -> None:
        """No upstream connection to rebuild — keep the running producers as-is."""
        return

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    async def _loop(self, asset: str, symbol: str, on_book: BookSink, on_status: StatusSink) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                base = await loop.run_in_executor(None, latest_mid, asset, symbol)
                if base is None or base <= 0:
                    on_status({"state": "unavailable", "error": f"no quote for {symbol}"})
                    await asyncio.sleep(self.UNAVAILABLE_DELAY)
                    continue
                step = random.gauss(0.0, self.JITTER_BPS / 1e4)      # noqa: S311 - not crypto-secure by design
                mid = base * (1.0 + step)
                imbalance = max(-0.9, min(0.9, step * 1e4 * self.IMBALANCE_GAIN))
                on_book(synthetic_book_frame(mid, imbalance=imbalance))
                await asyncio.sleep(self.TICK)
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001 - a provider hiccup must not kill the producer
                log.warning("sim depth %s:%s failed: %s — retrying in %.0fs", asset, symbol, e, RETRY_DELAY)
                on_status({"state": "reconnecting", "error": str(e)})
                await asyncio.sleep(RETRY_DELAY)


def build_depth_source(source: str) -> DepthSource | None:
    """Construct the depth producer for a source token, or ``None`` when unknown/unavailable.

    'sim' is built-in. Real vendors are imported lazily so their optional deps (e.g. ib_insync,
    databento) are only required when actually selected. 'exchange' (crypto real L2) and 'none' are
    handled upstream and never reach here.
    """
    s = (source or "").strip().lower()
    if s == "sim":
        return SimulatedDepthSource()
    if s == "ibkr":
        try:
            from app.services.depth_ibkr import IbkrDepthSource  # type: ignore[attr-defined]

            return IbkrDepthSource()
        except ImportError:
            log.warning("depth source 'ibkr' selected but its provider module is not installed")
            return None
    if s == "databento":
        try:
            from app.services.depth_databento import DatabentoDepthSource  # type: ignore[attr-defined]

            return DatabentoDepthSource()
        except ImportError:
            log.warning("depth source 'databento' selected but its provider module is not installed")
            return None
    if s == "dxfeed":
        try:
            from app.services.depth_dxfeed import DxFeedDepthSource  # type: ignore[attr-defined]

            return DxFeedDepthSource()
        except ImportError:
            log.warning("depth source 'dxfeed' selected but its provider module is not installed")
            return None
    return None
