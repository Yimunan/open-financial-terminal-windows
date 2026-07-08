"""Hands-on exploration of what yfinance actually returns — structure, quirks, quality, and
the metadata/fundamentals beyond OHLCV that could feed EquityMeta + the FundamentalsStore.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import yfinance as yf

from qhfi.core.types import AssetClass, DateRange, Instrument
from qhfi.data.providers.equities_yfinance import YFinanceDataProvider
from qhfi.data.quality import validate_bars


def hr(title):
    print(f"\n{'─' * 70}\n{title}\n{'─' * 70}")


def main():
    hr("1) Raw yf.download('AAPL', 2y) — what comes back before normalization")
    raw = yf.download("AAPL", period="2y", auto_adjust=True, progress=False)
    print("type        :", type(raw).__name__)
    print("shape       :", raw.shape)
    print("columns     :", list(raw.columns))
    print("col is multi:", hasattr(raw.columns, "levels"))
    print("index dtype :", raw.index.dtype, "| tz:", raw.index.tz)
    print("date range  :", raw.index.min().date(), "→", raw.index.max().date())
    bdays = len(raw)
    print(f"rows        : {bdays}  (~{bdays/2:.0f}/yr; ~252 trading days/yr is expected)")

    hr("2) Normalized through our provider + quality validation")
    ins = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)
    bars = YFinanceDataProvider().fetch_daily(ins, DateRange.model_construct(
        start=raw.index.min().date(), end=raw.index.max().date()))
    print("columns     :", list(bars.columns), "| tz:", bars.index.tz)
    print("dtypes      :", dict(bars.dtypes.astype(str)))
    rep = validate_bars(bars, ins, max_daily_return=0.25)
    print("quality     :", "OK" if rep.ok else rep.issues, "| fatal:", rep.fatal)
    print("max |daily move| :", f"{bars['close'].pct_change().abs().max():.1%}")
    print("calendar gaps >3d:", int((bars.index.to_series().diff().dt.days > 3).sum()),
          "(weekends/holidays — expected)")

    hr("3) Ticker.info — fields that map to EquityMeta")
    try:
        info = yf.Ticker("AAPL").info
        for k in ["sector", "industry", "industryKey", "marketCap", "country",
                  "currency", "sharesOutstanding", "averageVolume"]:
            print(f"  {k:18}: {info.get(k)}")

        hr("4) Ticker.info — fundamentals that could feed ValueFactor / QualityFactor")
        for k in ["trailingPE", "trailingEps", "priceToBook", "returnOnEquity",
                  "grossMargins", "debtToEquity", "dividendYield"]:
            v = info.get(k)
            print(f"  {k:18}: {v}")
        # value factor = earnings yield = E/P = 1/PE
        pe = info.get("trailingPE")
        if pe:
            print(f"  → earnings_yield (1/PE): {1/pe:.4f}")
    except Exception as e:  # noqa: BLE001
        print("  .info unavailable:", type(e).__name__, e)

    hr("5) Corporate actions + point-in-time fundamentals availability")
    try:
        tk = yf.Ticker("AAPL")
        print("dividends rows :", len(tk.dividends), "| splits rows:", len(tk.splits))
        qf = tk.quarterly_financials
        print("quarterly_financials shape:", qf.shape)
        print("  report dates (cols):", [str(c.date()) for c in qf.columns][:4])
        print("  → these are PERIOD-END dates, NOT filing dates — see PIT caveat below")
    except Exception as e:  # noqa: BLE001
        print("  financials unavailable:", type(e).__name__, e)


if __name__ == "__main__":
    main()
