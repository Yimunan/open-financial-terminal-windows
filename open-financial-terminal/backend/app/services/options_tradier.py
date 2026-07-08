"""Tradier options-chain source — chains + greeks + IV (greeks courtesy of ORATS).

Optional + gated: needs a Tradier API token in ``TRADIER_TOKEN`` (or ``OFT_TRADIER_TOKEN``); the free
**sandbox** works with delayed data (set ``TRADIER_ENV=sandbox``). Without a token ``enabled()`` is
False and the widget shows "unavailable". Uses httpx (already a dependency). Untested against a live
token in this checkout.
"""

from __future__ import annotations

import logging
import os

import httpx

from app import config
from app.services.options import OptionQuote

log = logging.getLogger("oft.options.tradier")

_LIVE = "https://api.tradier.com/v1"
_SANDBOX = "https://sandbox.tradier.com/v1"


def _token() -> str:
    # Prefer a token saved in Settings → Data Providers (encrypted on disk), then the env vars.
    return (config.get_provider_secret("tradier", "token")
            or os.getenv("TRADIER_TOKEN") or os.getenv("OFT_TRADIER_TOKEN") or "")


def _base() -> str:
    env = (config.get_provider_secret("tradier", "env") or os.getenv("TRADIER_ENV", "")).lower()
    return _SANDBOX if env == "sandbox" else _LIVE


def _as_list(v: object) -> list:
    """Tradier returns a dict for a single result, a list for many — normalize to a list."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class TradierOptionsSource:
    def enabled(self) -> bool:
        return bool(_token())

    def capabilities(self) -> dict:
        return config.OPTIONS_CAPS["tradier"]

    def _get(self, path: str, params: dict) -> dict:
        r = httpx.get(f"{_base()}{path}", params=params,
                      headers={"Authorization": f"Bearer {_token()}", "Accept": "application/json"},
                      timeout=15.0)
        r.raise_for_status()
        return r.json()

    def expirations(self, underlying: str) -> list[str]:
        data = self._get("/markets/options/expirations", {"symbol": underlying.upper()})
        exp = (data.get("expirations") or {}).get("date")
        return sorted(_as_list(exp))

    def chain(self, underlying: str, expiry: str) -> list[OptionQuote]:
        data = self._get("/markets/options/chains",
                         {"symbol": underlying.upper(), "expiration": expiry, "greeks": "true"})
        rows: list[OptionQuote] = []
        for o in _as_list((data.get("options") or {}).get("option")):
            g = o.get("greeks") or {}
            rows.append(OptionQuote(
                strike=float(o["strike"]), right="call" if o.get("option_type") == "call" else "put",
                bid=_f(o.get("bid")), ask=_f(o.get("ask")), last=_f(o.get("last")),
                volume=_i(o.get("volume")), open_interest=_i(o.get("open_interest")),
                iv=_f(g.get("mid_iv") or g.get("smv_vol")),
                delta=_f(g.get("delta")), gamma=_f(g.get("gamma")), theta=_f(g.get("theta")),
                vega=_f(g.get("vega")), rho=_f(g.get("rho")),
                in_the_money=None, contract_symbol=o.get("symbol"),
            ))
        return rows


def _f(x: object) -> float | None:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _i(x: object) -> int | None:
    v = _f(x)
    return int(v) if v is not None else None
