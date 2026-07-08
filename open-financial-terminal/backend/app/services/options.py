"""Equity-options chain subsystem — a pluggable OptionsSource seam + chain service.

Standalone (NOT a qhfi AssetClass / OHLCV category): options are chain-shaped (expiry × strike ×
call/put with bid/ask/IV/OI/greeks), so this parallels the depth-source seam rather than the bars
pipeline. `build_options_source(source)` returns a provider; the default `YFinanceOptionsSource`
gives chains + IV (no greeks) for free, and vendor modules (tradier/polygon/ibkr) plug in the same
way, gated on their SDK/creds. The chain service caches results (TTL) and, for sources without native
greeks, enriches each row with locally-computed Black-Scholes greeks (see services/greeks.py), using
the underlying spot from the existing quote path.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime
from typing import Literal, Protocol, TypedDict

from app import config
from app.services import greeks as gk

log = logging.getLogger("oft.options")

Right = Literal["call", "put"]


class OptionQuote(TypedDict):
    strike: float
    right: Right
    bid: float | None
    ask: float | None
    last: float | None
    volume: int | None
    open_interest: int | None
    iv: float | None            # decimal (0.28 == 28%)
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    rho: float | None
    in_the_money: bool | None
    contract_symbol: str | None  # OCC


class OptionsCaps(TypedDict):
    chains: bool
    iv: bool
    greeks: bool
    realtime: bool


class OptionsSource(Protocol):
    def enabled(self) -> bool: ...
    def capabilities(self) -> OptionsCaps: ...
    def expirations(self, underlying: str) -> list[str]: ...          # ISO 'YYYY-MM-DD', sorted
    def chain(self, underlying: str, expiry: str) -> list[OptionQuote]: ...  # calls+puts, greeks may be None


# ── helpers ──────────────────────────────────────────────────────────────────────────
def _f(x: object) -> float | None:
    try:
        v = float(x)  # type: ignore[arg-type]
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _price(x: object) -> float | None:
    v = _f(x)
    return v if (v is not None and v > 0) else None


def _int(x: object) -> int | None:
    v = _f(x)
    return int(v) if v is not None else None


def days_to_expiry(expiry: str) -> float:
    try:
        d = datetime.strptime(expiry, "%Y-%m-%d").date()
        return max(0, (d - date.today()).days)
    except (TypeError, ValueError):
        return 0


def is_monthly(expiry: str) -> bool:
    """True when the expiry is the standard 3rd-Friday monthly."""
    try:
        d = datetime.strptime(expiry, "%Y-%m-%d").date()
        return d.weekday() == 4 and 15 <= d.day <= 21
    except (TypeError, ValueError):
        return False


def occ_symbol(underlying: str, expiry: str, right: str, strike: float) -> str:
    """OCC contract id, e.g. occ_symbol('AAPL','2026-07-17','call',190) -> 'AAPL260717C00190000'."""
    d = datetime.strptime(expiry, "%Y-%m-%d").date()
    cp = "C" if right == "call" else "P"
    return f"{underlying.upper()}{d:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


def parse_occ(occ: str) -> tuple[str, str, str, float]:
    """OCC id → (underlying, expiry ISO, right, strike). Inverse of occ_symbol()."""
    s = occ.strip().upper()
    if len(s) < 16 or s[-9] not in ("C", "P"):
        raise ValueError(f"not an OCC option symbol: {occ}")
    root, body = s[:-15], s[-15:]
    expiry = datetime.strptime(body[:6], "%y%m%d").strftime("%Y-%m-%d")
    right = "call" if body[6] == "C" else "put"
    strike = int(body[7:]) / 1000.0
    return root, expiry, right, strike


def option_mark(occ: str) -> float | None:
    """Per-contract dollar mark for an OCC option (mid or last × 100), for paper pricing/P&L.

    Returns mark × 100 (one contract = 100 shares) so the generic paper broker's notional/P&L math
    (quantity × price) is correct with quantity = number of contracts. None when unpriceable."""
    try:
        underlying, expiry, right, strike = parse_occ(occ)
    except (ValueError, TypeError):
        return None
    data = chain(underlying, expiry)
    rows = data.get("calls" if right == "call" else "puts") or []
    row = next((r for r in rows if abs(r["strike"] - strike) < 1e-6), None)
    if row is None:
        return None
    m = _mid(row) or row.get("last")
    return m * 100.0 if m else None


# ── default provider: yfinance (chains + IV, no greeks) ────────────────────────────────
class YFinanceOptionsSource:
    """Free delayed chains + IV via yfinance; greeks are computed downstream (Black-Scholes)."""

    def enabled(self) -> bool:
        return True  # yfinance is always a dependency

    def capabilities(self) -> OptionsCaps:
        return config.OPTIONS_CAPS["yfinance"]  # type: ignore[return-value]

    def expirations(self, underlying: str) -> list[str]:
        import yfinance as yf

        try:
            return list(yf.Ticker(underlying.upper()).options or [])
        except Exception:  # noqa: BLE001 - unknown symbol / network
            return []

    def chain(self, underlying: str, expiry: str) -> list[OptionQuote]:
        import yfinance as yf

        oc = yf.Ticker(underlying.upper()).option_chain(expiry)
        rows: list[OptionQuote] = []
        for frame, right in ((oc.calls, "call"), (oc.puts, "put")):
            for _, r in frame.iterrows():
                rows.append(OptionQuote(
                    strike=float(r["strike"]), right=right,  # type: ignore[typeddict-item]
                    bid=_price(r.get("bid")), ask=_price(r.get("ask")), last=_price(r.get("lastPrice")),
                    volume=_int(r.get("volume")), open_interest=_int(r.get("openInterest")),
                    iv=_f(r.get("impliedVolatility")),
                    delta=None, gamma=None, theta=None, vega=None, rho=None,
                    in_the_money=bool(r.get("inTheMoney")) if r.get("inTheMoney") is not None else None,
                    contract_symbol=str(r.get("contractSymbol")) if r.get("contractSymbol") else None,
                ))
        return rows


def build_options_source(source: str) -> OptionsSource | None:
    """Construct the options-chain provider for a source token, or None when unknown/unavailable."""
    s = (source or "").strip().lower()
    if s in ("", "none"):
        return None
    if s == "yfinance":
        return YFinanceOptionsSource()
    if s == "tradier":
        try:
            from app.services.options_tradier import TradierOptionsSource  # type: ignore
            return TradierOptionsSource()
        except ImportError:
            log.warning("options source 'tradier' selected but its provider module is not installed")
            return None
    if s == "polygon":
        try:
            from app.services.options_polygon import PolygonOptionsSource  # type: ignore
            return PolygonOptionsSource()
        except ImportError:
            log.warning("options source 'polygon' selected but its provider module is not installed")
            return None
    if s == "ibkr":
        try:
            from app.services.options_ibkr import IbkrOptionsSource  # type: ignore
            return IbkrOptionsSource()
        except ImportError:
            log.warning("options source 'ibkr' selected but its provider module is not installed")
            return None
    return None


def get_active_source() -> OptionsSource | None:
    return build_options_source(config.get_options_source())


# ── chain service (TTL cache + greeks enrichment) ──────────────────────────────────────
_lock = threading.Lock()
_exp_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}          # (source, underlying) → (ts, expiries)
_chain_cache: dict[tuple[str, str, str], tuple[float, list[OptionQuote]]] = {}  # (source, u, expiry) → (ts, rows)


def clear_options_cache() -> int:
    with _lock:
        n = len(_exp_cache) + len(_chain_cache)
        _exp_cache.clear()
        _chain_cache.clear()
    return n


def expirations(underlying: str) -> dict:
    """Expirations for an underlying, filtered to the configured window, tagged dte + monthly."""
    src = get_active_source()
    source = config.get_options_source()
    if src is None or not src.enabled():
        return {"underlying": underlying.upper(), "source": source, "expirations": [],
                "note": f"options source '{source}' unavailable"}
    key = (source, underlying.upper())
    now = time.monotonic()
    with _lock:
        hit = _exp_cache.get(key)
        raw = hit[1] if hit and now - hit[0] < config.get_options_chain_ttl() else None
    if raw is None:
        try:
            raw = src.expirations(underlying)
        except Exception as e:  # noqa: BLE001 - unknown symbol / vendor / network
            return {"underlying": underlying.upper(), "source": source, "expirations": [],
                    "note": f"{type(e).__name__}: {e}"}
        with _lock:
            _exp_cache[key] = (now, raw)
    window = config.get_options_expiry_window()
    out = [{"date": e, "dte": days_to_expiry(e), "monthly": is_monthly(e)}
           for e in raw if 0 <= days_to_expiry(e) <= window]
    note = None if out else f"no options chain for {underlying.upper()}"
    return {"underlying": underlying.upper(), "source": source, "expirations": out, "note": note}


def chain(underlying: str, expiry: str) -> dict:
    """A normalized chain snapshot for (underlying, expiry) — calls|puts with greeks enriched."""
    src = get_active_source()
    source = config.get_options_source()
    if src is None or not src.enabled():
        return {"underlying": underlying.upper(), "expiry": expiry, "source": source,
                "calls": [], "puts": [], "strikes": [], "note": f"options source '{source}' unavailable"}
    key = (source, underlying.upper(), expiry)
    now = time.monotonic()
    with _lock:
        hit = _chain_cache.get(key)
        rows = hit[1] if hit and now - hit[0] < config.get_options_chain_ttl() else None
    if rows is None:
        try:
            rows = src.chain(underlying, expiry)
        except Exception as e:  # noqa: BLE001 - unknown symbol/expiry/network
            return {"underlying": underlying.upper(), "expiry": expiry, "source": source,
                    "calls": [], "puts": [], "strikes": [], "note": f"{type(e).__name__}: {e}"}
        with _lock:
            _chain_cache[key] = (now, rows)

    caps = config.get_options_caps(source)
    spot = None
    rate = None
    div = None
    greeks_computed = False
    note = None
    if not caps.get("greeks") and config.get_options_greeks_mode() == "auto":
        from app.services.depth import latest_mid

        spot = latest_mid("equity", underlying.upper())
        rate = gk.risk_free_rate()
        div = dividend_yield(underlying.upper())     # continuous div yield q → correct greeks/IV
        T = days_to_expiry(expiry) / 365.0
        if spot and T > 0:
            greeks_computed = True
            for row in rows:
                iv = row["iv"]
                if not iv or iv < 1e-4:          # missing, or the ~1e-5 garbage yfinance gives no-data contracts
                    mid = _mid(row)
                    iv = gk.implied_vol(mid, spot, row["strike"], T, rate, row["right"], div) if mid else None
                    row["iv"] = iv
                if iv and iv > 1e-4:
                    row.update(gk.bs_greeks(spot, row["strike"], T, rate, iv, row["right"], div))
        else:
            note = f"greeks unavailable: no spot for {underlying.upper()}" if not spot else None

    calls = sorted((r for r in rows if r["right"] == "call"), key=lambda r: r["strike"])
    puts = sorted((r for r in rows if r["right"] == "put"), key=lambda r: r["strike"])
    strikes = sorted({r["strike"] for r in rows})
    atm = min(strikes, key=lambda k: abs(k - spot)) if (strikes and spot) else None
    return {
        "underlying": underlying.upper(), "expiry": expiry,
        "dte": days_to_expiry(expiry), "monthly": is_monthly(expiry), "source": source,
        "spot": spot, "risk_free_rate": rate, "dividend_yield": div, "greeks_computed": greeks_computed,
        "atm_strike": atm, "strikes": strikes, "calls": calls, "puts": puts, "note": note,
    }


_div_cache: dict[str, tuple[float, float]] = {}
_DIV_TTL = 3600.0


def dividend_yield(underlying: str) -> float:
    """Trailing dividend yield (decimal fraction) for an equity underlying, cached ~1h; 0 if unknown.

    Prefers yfinance ``trailingAnnualDividendYield`` (an unambiguous fraction, e.g. 0.0034), since
    ``dividendYield`` is a percent in current yfinance (0.35 = 0.35%) and a fraction in older ones —
    so we divide that fallback by 100. Clamped to a sane [0, 0.15] to keep a bad value from distorting
    greeks (a wrong-but-large q is worse than 0)."""
    u = underlying.upper()
    now = time.monotonic()
    hit = _div_cache.get(u)
    if hit and now - hit[0] < _DIV_TTL:
        return hit[1]
    q = 0.0
    try:
        import yfinance as yf

        info = yf.Ticker(u).info or {}
        frac = info.get("trailingAnnualDividendYield")
        if frac is None:
            pct = info.get("dividendYield")
            frac = float(pct) / 100.0 if pct is not None else 0.0   # dividendYield is a percent today
        q = max(0.0, min(0.15, float(frac or 0.0)))
    except Exception:  # noqa: BLE001 - dividend yield is best-effort; 0.0 is a fine default
        q = 0.0
    _div_cache[u] = (now, q)
    return q


def _mid(row: OptionQuote) -> float | None:
    bid, ask, last = row["bid"], row["ask"], row["last"]
    if bid and ask:
        return (bid + ask) / 2.0
    return last
