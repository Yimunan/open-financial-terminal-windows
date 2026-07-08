"""Generate config/instruments/sp500.yaml from Wikipedia's S&P 500 constituents (which carry
GICS sectors). Current members only → survivorship-biased for historical backtests.

  .venv\\Scripts\\python.exe scripts\\build_sp500_universe.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx
import pandas as pd

from qhfi.core.types import AssetClass, EquityMeta, Instrument, Universe
from qhfi.core.universe_io import save_universe

# Raw CSV (datahub) — reliable for scripted access, carries GICS sectors. Wikipedia 403s bots.
URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
OUT = Path("config") / "instruments" / "sp500.yaml"


def main() -> None:
    r = httpx.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    sym_col = next(c for c in df.columns if c.lower() == "symbol")
    sec_col = next(c for c in df.columns if "sector" in c.lower())
    df = df[[sym_col, sec_col]].dropna()
    df.columns = ["Symbol", "GICS Sector"]

    instruments = []
    for sym, sector in df.itertuples(index=False):
        ticker = str(sym).replace(".", "-").strip()    # Wikipedia "BRK.B" → yfinance "BRK-B"
        instruments.append(Instrument(
            id=ticker, asset_class=AssetClass.EQUITY,
            equity=EquityMeta(gics_sector=str(sector).strip()),
        ))

    uni = Universe(name="sp500", instruments=instruments)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    save_universe(uni, OUT)

    dist = df["GICS Sector"].value_counts()
    print(f"Wrote {OUT} with {len(instruments)} names across {dist.size} GICS sectors\n")
    print(dist.to_string())


if __name__ == "__main__":
    main()
