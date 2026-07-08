"""Interactive Brokers order-book depth source (TWS API market depth).

Streams real Level-II depth for equities, rates futures, spot FX (IdealPro) and commodity futures
from a running **IB Gateway / TWS**, via the maintained ``ib_async`` package (falls back to legacy
``ib_insync`` — same API). Optional + gated: with neither the package nor a reachable gateway,
``enabled()`` is False and the widget shows an empty book instead of crashing.

Connection (env): ``OFT_IBKR_HOST`` (127.0.0.1), ``OFT_IBKR_PORT`` (4002 = IB Gateway paper /
7497 = TWS paper), ``OFT_IBKR_CLIENT_ID`` (17). Depth additionally needs the relevant L2
market-data subscriptions on the IB account (e.g. NASDAQ TotalView for US equities; CME/CBOT/COMEX/
NYMEX bundles for futures). NOTE: IB caps concurrent market-depth lines, so only a few OrderBook
widgets can stream depth simultaneously.

ib_async runs on its own asyncio loop, so this owns one connection on a dedicated daemon thread and
marshals reqMktDepth updates into the hub sink. Untested against a live gateway in this checkout.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from app.services.realtime import BOOK_DEPTH

log = logging.getLogger("oft.depth.ibkr")

# Futures root → IB listing exchange (rates + commodities). Unknown roots fall back to GLOBEX.
_FUT_EXCHANGE = {
    "ZT": "CBOT", "ZF": "CBOT", "ZN": "CBOT", "ZB": "CBOT", "UB": "CBOT", "ZQ": "CBOT",
    "GC": "COMEX", "SI": "COMEX", "HG": "COMEX", "PL": "NYMEX", "PA": "NYMEX",
    "CL": "NYMEX", "BZ": "NYMEX", "NG": "NYMEX", "RB": "NYMEX", "HO": "NYMEX",
    "ZC": "CBOT", "ZS": "CBOT", "ZW": "CBOT", "ZL": "CBOT", "ZM": "CBOT",
    "KC": "NYBOT", "SB": "NYBOT", "CC": "NYBOT", "CT": "NYBOT", "OJ": "NYBOT",
}


def _import_ib() -> Any:
    """The ib_async module (maintained), else legacy ib_insync, else None."""
    try:
        import ib_async as ib  # type: ignore
        return ib
    except Exception:  # noqa: BLE001
        try:
            import ib_insync as ib  # type: ignore
            return ib
        except Exception:  # noqa: BLE001
            return None


def _contract(ib: Any, asset: str, symbol: str) -> Any:
    """Map (asset, clean symbol) → an IB contract, or None for an unsupported class."""
    a, s = asset.lower(), symbol.upper()
    if a == "equity":
        return ib.Stock(s, "SMART", "USD")
    if a == "fx":
        return ib.Forex(s.replace("/", ""))            # 'EUR/USD' → 'EURUSD' (IdealPro)
    if a in ("rates", "commodity"):
        root = s.split("/")[0].split()[0]
        return ib.ContFuture(root, _FUT_EXCHANGE.get(root, "GLOBEX"))  # continuous front month
    return None


class IbkrDepthSource:
    """Owns one IB connection on a background thread; bridges reqMktDepth → BookFrame sinks."""

    def __init__(self) -> None:
        self._ib_mod: Any = None
        self._ib: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tickers: dict[tuple[str, str], Any] = {}
        # Prefer host/port saved in Settings → Data Providers, then the env vars. Rebuilt on each
        # reload_market_data(), so a saved change takes effect after the depth reset.
        from app.config import get_provider_config

        _ib = get_provider_config().get("ibkr", {})
        self._host = _ib.get("host") or os.getenv("OFT_IBKR_HOST", "127.0.0.1")
        try:
            self._port = int(_ib.get("port") or os.getenv("OFT_IBKR_PORT", "4002") or 4002)
        except (TypeError, ValueError):
            self._port = 4002
        self._cid = int(os.getenv("OFT_IBKR_CLIENT_ID", "17") or 17)

    def enabled(self, asset: str) -> bool:
        return _import_ib() is not None and asset.lower() in ("equity", "rates", "fx", "commodity")

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ib_mod = _import_ib()
        if self._ib_mod is None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="ibkr-depth", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ib = self._ib_mod.IB()
        try:
            loop.run_until_complete(self._ib.connectAsync(self._host, self._port, clientId=self._cid))
        except Exception as e:  # noqa: BLE001 - gateway down / wrong port
            log.warning("IB connect %s:%s failed: %s", self._host, self._port, e)
            self._ib = None
            return
        loop.run_forever()

    def subscribe(self, asset: str, symbol: str, on_book: Any, on_status: Any) -> None:
        self._ensure_thread()
        if self._loop is None:
            on_status({"state": "unavailable", "error": "ib_async/ib_insync not installed"})
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._subscribe(asset, symbol, on_book, on_status)),
        )

    async def _subscribe(self, asset: str, symbol: str, on_book: Any, on_status: Any) -> None:
        if self._ib is None or not self._ib.isConnected():
            on_status({"state": "unavailable",
                       "error": f"IB gateway not reachable at {self._host}:{self._port}"})
            return
        try:
            contract = _contract(self._ib_mod, asset, symbol)
            if contract is None:
                on_status({"state": "unavailable", "error": f"no IB contract for {asset} {symbol}"})
                return
            await self._ib.qualifyContractsAsync(contract)
            smart = asset.lower() == "equity"
            ticker = self._ib.reqMktDepth(contract, numRows=min(BOOK_DEPTH, 20), isSmartDepth=smart)
            self._tickers[(asset, symbol)] = ticker

            def _on_update(t: Any = ticker) -> None:
                bids = [[float(l.price), float(l.size)] for l in (t.domBids or [])][:BOOK_DEPTH]
                asks = [[float(l.price), float(l.size)] for l in (t.domAsks or [])][:BOOK_DEPTH]
                if bids and asks:
                    on_book({"bids": bids, "asks": asks, "ts": None})

            ticker.updateEvent += _on_update
        except Exception as e:  # noqa: BLE001 - qualify/subscribe failure
            on_status({"state": "reconnecting", "error": str(e)})

    def unsubscribe(self, asset: str, symbol: str) -> None:
        ticker = self._tickers.pop((asset, symbol), None)
        if ticker is not None and self._ib is not None and self._loop is not None:
            smart = asset.lower() == "equity"
            self._loop.call_soon_threadsafe(
                lambda: self._ib.cancelMktDepth(ticker.contract, isSmartDepth=smart),
            )

    async def reset(self) -> None:
        await self.close()

    async def close(self) -> None:
        loop, ib = self._loop, self._ib
        if loop is not None:
            def _shutdown() -> None:
                try:
                    if ib is not None:
                        ib.disconnect()
                except Exception:  # noqa: BLE001 - best-effort
                    pass
                loop.stop()
            loop.call_soon_threadsafe(_shutdown)
        self._tickers.clear()
        self._ib = None
        self._loop = None
        self._thread = None
