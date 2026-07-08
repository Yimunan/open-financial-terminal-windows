"""Probe availability of rates futures + options data via yfinance (what's free/reachable)."""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import yfinance as yf

# CME rates futures — Yahoo continuous front-month tickers
RATES_FUT = {"ZT=F": "2Y T-Note", "ZF=F": "5Y T-Note", "ZN=F": "10Y T-Note",
             "ZB=F": "30Y T-Bond", "UB=F": "Ultra Bond", "ZQ=F": "30D Fed Funds", "SR3=F": "3M SOFR"}


def main() -> None:
    print("=== RATES FUTURES (yfinance continuous front-month) ===")
    data = yf.download(list(RATES_FUT), period="5d", auto_adjust=False, progress=False)
    close = data["Close"] if "Close" in data else data
    for tk, label in RATES_FUT.items():
        if tk in close.columns and close[tk].notna().any():
            s = close[tk].dropna()
            print(f"  {tk:<7} {label:<14} ✓  latest {s.iloc[-1]:.3f}  ({len(s)} of 5 days)")
        else:
            print(f"  {tk:<7} {label:<14} ✗  no data")

    print("\n=== OPTIONS (yfinance — current chains, equities only) ===")
    tk = yf.Ticker("AAPL")
    try:
        exps = tk.options
        print(f"  AAPL: {len(exps)} expiries available, e.g. {list(exps[:3])}")
        oc = tk.option_chain(exps[0])
        print(f"  chain[{exps[0]}]: {len(oc.calls)} calls / {len(oc.puts)} puts")
        print(f"  columns: {list(oc.calls.columns)}")
        row = oc.calls.iloc[len(oc.calls) // 2]
        print(f"  sample call: strike {row['strike']}  IV {row.get('impliedVolatility'):.3f}  "
              f"bid {row.get('bid')}  OI {row.get('openInterest')}")
    except Exception as e:  # noqa: BLE001
        print(f"  options unavailable: {type(e).__name__} {e}")

    print("\n  Rates options (options on ZN/ZB futures, SOFR options): NOT on yfinance.")


if __name__ == "__main__":
    main()
