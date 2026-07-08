"""Pluggable time-&-sales (trade prints / "the tape") sources → realtime-hub bridge.

The realtime hub ([services/realtime.py]) streams a live tape for crypto (real ccxt.pro
``watch_trades``) and equities (Alpaca prints). Rates, FX and commodities have no free market-wide
print feed. So this module introduces a **vendor-agnostic trades-source seam** — the mirror of
``services/depth.py`` for the tape: the hub routes a ``trades:<source>.<asset>:<symbol>`` topic here,
and a ``TradesSource`` for that source emits ``{price, amount, side, ts}`` prints which flow out
through the hub's existing flusher (append-buffered + drained, so no prints are lost).

The one producer shipped today is :class:`SimulatedTradesSource` — a local feed (no credentials)
that emits modelled prints around the *real* mid (yfinance/ccxt via ``depth.latest_mid``), so Time &
Sales works end-to-end for every asset class, not just crypto. It is honest about being synthetic
(the widget tags it). A real vendor (IBKR, Databento, …) plugs into the SAME seam later: implement
:class:`TradesSource` in a sibling module and add one branch to :func:`build_trades_source` — nothing
else in the hub, health or the frontend changes.

Mirrors ``services/depth.py`` (lazy start, per-topic sink callbacks, ``reset()``/``close()``) and
reuses ``depth.latest_mid`` for the cross-asset mid so the mapping never drifts.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Callable, Protocol

from app.services.depth import latest_mid
from app.services.realtime import RETRY_DELAY

log = logging.getLogger("oft.trades")

#: A single print in the on-the-wire ``TradeFrame`` shape ``{price, amount, side, ts}``.
TradeSink = Callable[[dict], None]
#: A hub status frame, ``{"state": ..., "error": ...}`` (e.g. unavailable / reconnecting).
StatusSink = Callable[[dict], None]


class TradesSource(Protocol):
    """A pluggable trade-print producer. Poll (sim) or push (vendor) — both behind this interface."""

    def enabled(self, asset: str) -> bool: ...
    def subscribe(self, asset: str, symbol: str, on_trade: TradeSink, on_status: StatusSink) -> None: ...
    def unsubscribe(self, asset: str, symbol: str) -> None: ...
    async def reset(self) -> None: ...
    async def close(self) -> None: ...


class SimulatedTradesSource:
    """Local simulated tape: modelled prints around the real mid, emitted on a jittered cadence.

    No credentials, always available. One asyncio task per ``(asset, symbol)`` refetches the mid
    periodically (blocking network call, off-loop) and, between refetches, emits synthetic prints at
    random sub-second intervals: price jittered a few bps around the mid, a random side, and an
    asset-appropriate size. Honest about being synthetic — the widget tags it so it is never mistaken
    for a real vendor tape.
    """

    REFRESH = 6.0              # seconds between mid refetches (prints are emitted in between)
    UNAVAILABLE_DELAY = 5.0    # backoff when no mid is obtainable (market closed / unknown symbol)
    JITTER_BPS = 2.5           # per-print price jitter around the mid (bps, stdev)
    MIN_GAP = 0.25             # min seconds between prints
    MAX_GAP = 1.5              # max seconds between prints

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def enabled(self, asset: str) -> bool:  # noqa: ARG002 - always available, no creds
        return True

    def subscribe(self, asset: str, symbol: str, on_trade: TradeSink, on_status: StatusSink) -> None:
        key = (asset, symbol)
        old = self._tasks.pop(key, None)
        if old is not None:
            old.cancel()
        self._tasks[key] = asyncio.create_task(
            self._loop(asset, symbol, on_trade, on_status), name=f"sim-trades-{asset}:{symbol}",
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

    @staticmethod
    def _size(asset: str) -> float:
        """A plausible random print size for the asset class (coins / shares / contracts)."""
        a = (asset or "").lower()
        if a == "crypto":
            return round(random.uniform(0.005, 4.0), 6)      # noqa: S311 - synthetic by design
        if a == "equity":
            return float(random.randint(5, 750))             # noqa: S311 - share lots
        return float(random.randint(1, 40))                  # noqa: S311 - FICC contracts

    async def _loop(self, asset: str, symbol: str, on_trade: TradeSink, on_status: StatusSink) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                base = await loop.run_in_executor(None, latest_mid, asset, symbol)
                if base is None or base <= 0:
                    on_status({"state": "unavailable", "error": f"no quote for {symbol}"})
                    await asyncio.sleep(self.UNAVAILABLE_DELAY)
                    continue
                elapsed = 0.0
                while elapsed < self.REFRESH:
                    gap = random.uniform(self.MIN_GAP, self.MAX_GAP)                 # noqa: S311
                    await asyncio.sleep(gap)
                    elapsed += gap
                    px = base * (1.0 + random.gauss(0.0, self.JITTER_BPS / 1e4))     # noqa: S311
                    side = "buy" if random.random() < 0.5 else "sell"               # noqa: S311
                    on_trade({"price": round(px, 8), "amount": self._size(asset),
                              "side": side, "ts": int(time.time() * 1000)})
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001 - a provider hiccup must not kill the producer
                log.warning("sim trades %s:%s failed: %s — retrying in %.0fs", asset, symbol, e, RETRY_DELAY)
                on_status({"state": "reconnecting", "error": str(e)})
                await asyncio.sleep(RETRY_DELAY)


def build_trades_source(source: str) -> TradesSource | None:
    """Construct the trades producer for a source token, or ``None`` when unknown/unavailable.

    'sim' is built-in. Real vendors are imported lazily so their optional deps are only required when
    actually selected. 'exchange' (crypto real tape) and 'alpaca' (equity real tape) are handled
    upstream (ccxt.pro / Alpaca stream) and never reach here.
    """
    s = (source or "").strip().lower()
    if s == "sim":
        return SimulatedTradesSource()
    if s == "ibkr":
        try:
            from app.services.trades_ibkr import IbkrTradesSource  # type: ignore[attr-defined]

            return IbkrTradesSource()
        except ImportError:
            log.warning("trades source 'ibkr' selected but its provider module is not installed")
            return None
    if s == "databento":
        try:
            from app.services.trades_databento import DatabentoTradesSource  # type: ignore[attr-defined]

            return DatabentoTradesSource()
        except ImportError:
            log.warning("trades source 'databento' selected but its provider module is not installed")
            return None
    return None
