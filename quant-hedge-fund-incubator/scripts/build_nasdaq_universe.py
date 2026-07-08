"""Generate config/instruments/nasdaq_composite.yaml from the authoritative Nasdaq Trader
symbol directory (nasdaqlisted.txt). Approximates the Nasdaq Composite: Nasdaq-listed,
non-ETF, non-test securities. No GICS sectors (not available at this scale).

  .venv\\Scripts\\python.exe scripts\\build_nasdaq_universe.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx
import pandas as pd

from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.core.universe_io import save_universe

URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OUT = Path("config") / "instruments" / "nasdaq_composite.yaml"


def main() -> None:
    r = httpx.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|")
    df = df[df["Test Issue"].isin(["N"])]                 # drop test issues + footer row
    total_listed = len(df)

    common = df[df["ETF"] == "N"].copy()                  # exclude ETFs → ~Composite eligible
    excl_etf = total_listed - len(common)

    instruments, skipped_special = [], 0
    for sym in common["Symbol"].dropna().astype(str):
        if not sym or sym.lower() == "nan" or any(c in sym for c in "$= "):
            skipped_special += 1
            continue
        instruments.append(Instrument(id=sym.replace(".", "-"), asset_class=AssetClass.EQUITY))

    uni = Universe(name="nasdaq_composite", instruments=instruments)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    save_universe(uni, OUT)
    print(f"Nasdaq-listed (non-test): {total_listed}")
    print(f"  excluded ETFs        : {excl_etf}")
    print(f"  excluded special syms: {skipped_special}")
    print(f"→ wrote {OUT} with {len(instruments)} names (no GICS sectors)")


if __name__ == "__main__":
    main()
