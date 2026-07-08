"""Black-Scholes option pricing, greeks, and implied-vol solver + a risk-free rate source.

Used to enrich option chains from sources that give IV but no greeks (yfinance): given the spot, a
risk-free rate, and either the source IV or an IV solved from the option mid, we compute
delta/gamma/theta/vega/rho locally. Pure stdlib (`math` — normal CDF via `erf`), no scipy/numpy.

Trader conventions on the returned greeks: theta is per **calendar day** (annual ÷ 365), vega is per
**1 vol point** (raw × 0.01), rho is per **1% rate** (raw × 0.01). delta/gamma are standard.
"""

from __future__ import annotations

import logging
import math
import time

log = logging.getLogger("oft.greeks")

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float, q: float) -> tuple[float, float]:
    vol_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
    return d1, d1 - vol_t


def bs_price(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> float:
    """Black-Scholes-Merton price of a European call/put (continuous dividend yield q)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # intrinsic at/after expiry (or degenerate inputs)
        intrinsic = (S - K) if right == "call" else (K - S)
        return max(0.0, intrinsic)
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    if right == "call":
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, right: str, q: float = 0.0) -> dict:
    """delta/gamma/theta(per day)/vega(per vol pt)/rho(per 1%) — or all None on degenerate inputs."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
    d1, d2 = _d1_d2(S, K, T, r, sigma, q)
    pdf = _norm_pdf(d1)
    disc_q = math.exp(-q * T)
    disc_r = math.exp(-r * T)
    gamma = disc_q * pdf / (S * sigma * math.sqrt(T))
    vega = S * disc_q * pdf * math.sqrt(T)             # per 1.0 vol
    if right == "call":
        delta = disc_q * _norm_cdf(d1)
        theta = (-(S * disc_q * pdf * sigma) / (2 * math.sqrt(T))
                 - r * K * disc_r * _norm_cdf(d2) + q * S * disc_q * _norm_cdf(d1))
        rho = K * T * disc_r * _norm_cdf(d2)           # per 1.0 r
    else:
        delta = disc_q * (_norm_cdf(d1) - 1.0)
        theta = (-(S * disc_q * pdf * sigma) / (2 * math.sqrt(T))
                 + r * K * disc_r * _norm_cdf(-d2) - q * S * disc_q * _norm_cdf(-d1))
        rho = -K * T * disc_r * _norm_cdf(-d2)
    return {
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "theta": round(theta / 365.0, 6),     # per calendar day
        "vega": round(vega * 0.01, 6),        # per 1 vol point
        "rho": round(rho * 0.01, 6),          # per 1% rate
    }


def implied_vol(price: float, S: float, K: float, T: float, r: float, right: str,
                q: float = 0.0) -> float | None:
    """Solve BS implied vol from an option price. Newton-Raphson, bisection fallback. None if no fit."""
    if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if right == "call" else (K - S)) * math.exp(-q * T)
    if price < intrinsic - 1e-6:          # below intrinsic → no real IV
        return None
    lo, hi = 1e-4, 5.0
    sigma = 0.25
    for _ in range(60):                   # Newton
        p = bs_price(S, K, T, r, sigma, right, q)
        diff = p - price
        if abs(diff) < 1e-6:
            return round(sigma, 6)
        d1, _ = _d1_d2(S, K, T, r, sigma, q)
        vega = S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T)
        if vega < 1e-8:
            break
        sigma -= diff / vega
        if sigma <= lo or sigma >= hi or math.isnan(sigma):
            break
    lo, hi = 1e-4, 5.0                     # bisection fallback
    if (bs_price(S, K, T, r, lo, right, q) - price) * (bs_price(S, K, T, r, hi, right, q) - price) > 0:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, r, mid, right, q) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-6:
            return round(0.5 * (lo + hi), 6)
    return round(0.5 * (lo + hi), 6)


_rate_cache: tuple[float, float] | None = None   # (monotonic_ts, rate)
_RATE_TTL = 3600.0                                # 1h
_RATE_FALLBACK = 0.04


def risk_free_rate() -> float:
    """Short risk-free rate (decimal). yfinance ^IRX (13-week T-bill) latest / 100, cached ~1h;
    falls back to 0.04 when unavailable. Good enough for greeks; ^IRX ≈ the 3M rate."""
    global _rate_cache
    now = time.monotonic()
    if _rate_cache is not None and now - _rate_cache[0] < _RATE_TTL:
        return _rate_cache[1]
    rate = _RATE_FALLBACK
    try:
        import yfinance as yf

        hist = yf.Ticker("^IRX").history(period="5d")
        if not hist.empty:
            val = float(hist["Close"].iloc[-1])
            if 0.0 <= val < 100.0:
                rate = val / 100.0
    except Exception:  # noqa: BLE001 - rate is best-effort; keep the fallback
        pass
    _rate_cache = (now, rate)
    return rate
