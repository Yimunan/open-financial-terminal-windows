"""Interactive Brokers options-chain source — chains + model greeks + IV via a running IB Gateway.

Optional + gated: needs ``ib_async`` (or legacy ``ib_insync``) AND a reachable IB Gateway/TWS
(``OFT_IBKR_HOST``/``OFT_IBKR_PORT``/``OFT_IBKR_CLIENT_ID`` — same env as the depth source) plus an
options market-data subscription. Without them ``enabled()`` is False and the widget shows
"unavailable".

IB serves chains via ``reqSecDefOptParams`` (expiries + strikes) then ``reqTickers`` with model greeks
per contract — which is pacing-limited and slow for a full chain, so this bounds the strike set to
``_MAX_STRIKES`` nearest the underlying last price. Runs on a dedicated background asyncio loop (IB's
API is loop-bound), marshalling results back synchronously. Untested against a live gateway here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime

from app import config
from app.services.options import OptionQuote

log = logging.getLogger("oft.options.ibkr")

_MAX_STRIKES = 40      # nearest-the-money strikes to price (IB reqTickers is pacing-limited)
_TIMEOUT = 30.0        # seconds for a chain request


def _import_ib():
    try:
        import ib_async as ib  # type: ignore
        return ib
    except Exception:  # noqa: BLE001
        try:
            import ib_insync as ib  # type: ignore
            return ib
        except Exception:  # noqa: BLE001
            return None


class IbkrOptionsSource:
    def __init__(self) -> None:
        self._ib_mod = None
        self._ib = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Prefer host/port saved in Settings → Data Providers, then the env vars (rebuilt on reload).
        from app.config import get_provider_config

        _ib = get_provider_config().get("ibkr", {})
        self._host = _ib.get("host") or os.getenv("OFT_IBKR_HOST", "127.0.0.1")
        try:
            self._port = int(_ib.get("port") or os.getenv("OFT_IBKR_PORT", "4002") or 4002)
        except (TypeError, ValueError):
            self._port = 4002
        self._cid = int(os.getenv("OFT_IBKR_CLIENT_ID", "18") or 18)   # distinct from the depth client id

    def enabled(self) -> bool:
        return _import_ib() is not None

    def capabilities(self) -> dict:
        return config.OPTIONS_CAPS["ibkr"]

    # ── connection on a dedicated loop/thread ──────────────────────────────────────────
    def _ensure(self) -> bool:
        if self._loop is not None and self._ib is not None and self._ib.isConnected():
            return True
        self._ib_mod = _import_ib()
        if self._ib_mod is None:
            return False
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run_loop, name="ibkr-options", daemon=True)
            self._thread.start()
            # wait briefly for connect
            for _ in range(30):
                if self._loop is not None and self._ib is not None and self._ib.isConnected():
                    break
                threading.Event().wait(0.1)
        return self._ib is not None and self._ib.isConnected()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ib = self._ib_mod.IB()
        try:
            loop.run_until_complete(self._ib.connectAsync(self._host, self._port, clientId=self._cid))
        except Exception as e:  # noqa: BLE001
            log.warning("IB options connect %s:%s failed: %s", self._host, self._port, e)
            self._ib = None
            return
        loop.run_forever()

    def _call(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
        return fut.result(timeout=_TIMEOUT)

    # ── OptionsSource API ───────────────────────────────────────────────────────────────
    def expirations(self, underlying: str) -> list[str]:
        if not self._ensure():
            return []
        return self._call(self._expirations(underlying))

    async def _expirations(self, underlying: str) -> list[str]:
        ib = self._ib_mod
        stock = ib.Stock(underlying.upper(), "SMART", "USD")
        await self._ib.qualifyContractsAsync(stock)
        params = await self._ib.reqSecDefOptParamsAsync(stock.symbol, "", "STK", stock.conId)
        exps: set[str] = set()
        for p in params:
            exps.update(p.expirations)         # 'YYYYMMDD'
        return sorted(_iso(e) for e in exps)

    def chain(self, underlying: str, expiry: str) -> list[OptionQuote]:
        if not self._ensure():
            return []
        return self._call(self._chain(underlying, expiry))

    async def _chain(self, underlying: str, expiry: str) -> list[OptionQuote]:
        ib = self._ib_mod
        u = underlying.upper()
        stock = ib.Stock(u, "SMART", "USD")
        await self._ib.qualifyContractsAsync(stock)
        [tk] = await self._ib.reqTickersAsync(stock)
        spot = tk.marketPrice() or tk.close or 0.0
        params = await self._ib.reqSecDefOptParamsAsync(stock.symbol, "", "STK", stock.conId)
        strikes = sorted({s for p in params for s in p.strikes})
        if spot and len(strikes) > _MAX_STRIKES:                 # keep the nearest-the-money band
            strikes = sorted(strikes, key=lambda k: abs(k - spot))[:_MAX_STRIKES]
            strikes.sort()
        ib_exp = expiry.replace("-", "")
        exch = params[0].exchange if params else "SMART"
        contracts = [ib.Option(u, ib_exp, k, r, exch)
                     for k in strikes for r in ("C", "P")]
        contracts = await self._ib.qualifyContractsAsync(*contracts)
        tickers = await self._ib.reqTickersAsync(*[c for c in contracts if c])
        rows: list[OptionQuote] = []
        for t in tickers:
            c = t.contract
            g = t.modelGreeks
            rows.append(OptionQuote(
                strike=float(c.strike), right="call" if c.right == "C" else "put",
                bid=_pos(t.bid), ask=_pos(t.ask), last=_pos(t.last),
                volume=int(t.volume) if t.volume == t.volume else None,
                open_interest=None,
                iv=_num(g.impliedVol) if g else None,
                delta=_num(g.delta) if g else None, gamma=_num(g.gamma) if g else None,
                theta=_num(g.theta) if g else None, vega=_num(g.vega) if g else None, rho=None,
                in_the_money=None, contract_symbol=c.localSymbol or None,
            ))
        return rows


def _iso(yyyymmdd: str) -> str:
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return yyyymmdd


def _num(x: object) -> float | None:
    try:
        v = float(x)  # type: ignore[arg-type]
        return None if v != v else v           # drop NaN
    except (TypeError, ValueError):
        return None


def _pos(x: object) -> float | None:
    v = _num(x)
    return v if (v is not None and v > 0) else None
