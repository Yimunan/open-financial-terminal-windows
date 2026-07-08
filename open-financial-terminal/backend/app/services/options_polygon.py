"""Polygon.io options-chain source — chains + greeks + IV via the snapshot API.

Optional + gated: needs ``POLYGON_API_KEY`` (or ``OFT_POLYGON_API_KEY``); without it ``enabled()`` is
False. Real-time greeks/IV require a plan that includes options quotes — the delayed/EOD snapshot
still returns greeks + IV. Uses httpx (already a dependency). Untested against a live key here.
"""

from __future__ import annotations

import logging
import os

import httpx

from app import config
from app.services.options import OptionQuote

log = logging.getLogger("oft.options.polygon")

_BASE = "https://api.polygon.io"


def _key() -> str:
    # Prefer a key saved in Settings → Data Providers (encrypted on disk), then the env vars.
    return (config.get_provider_secret("polygon", "api_key")
            or os.getenv("POLYGON_API_KEY") or os.getenv("OFT_POLYGON_API_KEY") or "")


class PolygonOptionsSource:
    def enabled(self) -> bool:
        return bool(_key())

    def capabilities(self) -> dict:
        return config.OPTIONS_CAPS["polygon"]

    def _get(self, path: str, params: dict) -> dict:
        r = httpx.get(f"{_BASE}{path}", params={**params, "apiKey": _key()}, timeout=15.0)
        r.raise_for_status()
        return r.json()

    def expirations(self, underlying: str) -> list[str]:
        # Distinct expiration dates from the (non-expired) contracts reference endpoint.
        dates: set[str] = set()
        params = {"underlying_ticker": underlying.upper(), "expired": "false", "limit": 1000}
        data = self._get("/v3/reference/options/contracts", params)
        for c in data.get("results", []) or []:
            if c.get("expiration_date"):
                dates.add(c["expiration_date"])
        return sorted(dates)

    def chain(self, underlying: str, expiry: str) -> list[OptionQuote]:
        rows: list[OptionQuote] = []
        data = self._get(f"/v3/snapshot/options/{underlying.upper()}",
                         {"expiration_date": expiry, "limit": 250})
        for c in data.get("results", []) or []:
            det = c.get("details") or {}
            g = c.get("greeks") or {}
            q = c.get("last_quote") or {}
            day = c.get("day") or {}
            rows.append(OptionQuote(
                strike=_f(det.get("strike_price")) or 0.0,
                right="call" if det.get("contract_type") == "call" else "put",
                bid=_f(q.get("bid")), ask=_f(q.get("ask")), last=_f((c.get("last_trade") or {}).get("price")),
                volume=_i(day.get("volume")), open_interest=_i(c.get("open_interest")),
                iv=_f(c.get("implied_volatility")),
                delta=_f(g.get("delta")), gamma=_f(g.get("gamma")), theta=_f(g.get("theta")),
                vega=_f(g.get("vega")), rho=None,
                in_the_money=None, contract_symbol=det.get("ticker"),
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
