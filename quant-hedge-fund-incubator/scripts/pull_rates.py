"""Pull the US Treasury yield curve from FRED into the `rates` lake category.

  .venv\\Scripts\\python.exe scripts\\pull_rates.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx
import pandas as pd

from qhfi.api.client import ManagedClient
from qhfi.data.lake import lake_root
from qhfi.data.providers.fred import FredRatesProvider
from qhfi.data.rates import RatesStore

# yfinance Treasury-yield tickers (fallback when FRED is unreachable) — fewer tenors
_YF_TENORS = {"3M": "^IRX", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX"}


def from_fred() -> pd.DataFrame:
    # fast-fail: short timeout, no retries
    p = FredRatesProvider(
        http=httpx.Client(timeout=8.0, follow_redirects=True, headers={"User-Agent": "qhfi-research"}),
        managed=ManagedClient(rate_per_sec=5.0, max_retries=0, backoff_base=0.0))
    return p.treasury_curve()


def from_yfinance() -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(list(_YF_TENORS.values()), period="max", auto_adjust=False, progress=False)["Close"]
    curve = raw.rename(columns={v: k for k, v in _YF_TENORS.items()})[list(_YF_TENORS)]
    if curve.stack().median() > 20:        # some Yahoo tickers quote yield ×10 (^TNX=42 → 4.2%)
        curve = curve / 10.0
    curve.index = pd.to_datetime(curve.index, utc=True)
    return curve.dropna(how="all").sort_index()


def main() -> None:
    store = RatesStore(lake_root())
    print(f"Pulling Treasury curve → {store.data_dir.resolve()}")
    try:
        curve, source = from_fred(), "FRED (full curve)"
    except Exception as e:  # noqa: BLE001
        print(f"  FRED unreachable ({type(e).__name__}) → falling back to yfinance rate tickers")
        curve, source = from_yfinance(), "yfinance (4 tenors)"
    print(f"  source: {source}")
    store.save("treasury_curve", curve)

    print(f"\nCurve: {curve.shape[0]:,} days × {curve.shape[1]} tenors "
          f"({curve.index.min().date()} → {curve.index.max().date()})")
    print("Tenor coverage (first date with data):")
    for tenor in curve.columns:
        s = curve[tenor].dropna()
        print(f"  {tenor:>3}: {s.index.min().date()} → {s.index.max().date()}  ({len(s):,} obs)")

    latest = curve.ffill().iloc[-1]
    print(f"\nLatest curve ({curve.index.max().date()}, %):")
    print("  " + "  ".join(f"{t}={latest[t]:.2f}" for t in curve.columns))
    short_t = curve.columns[0]                     # shortest available tenor
    spread = latest["10Y"] - latest[short_t]
    print(f"  term spread (10Y - {short_t}): {spread:+.2f}%  ({'inverted' if spread < 0 else 'normal'})")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
