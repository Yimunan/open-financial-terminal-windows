"""Generate config/instruments/nasdaq100.yaml. Fetches the current Nasdaq-100 constituents
(slickcharts; falls back to a bundled list if unreachable), assigns GICS sectors by reusing
the S&P 500 sector map where names overlap + a manual map for the non-S&P names, then verifies
coverage against the existing lakes (no new download expected — all are Nasdaq-listed).

  .venv\\Scripts\\python.exe scripts\\build_nasdaq100_universe.py
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
from qhfi.core.universe_io import load_universe, save_universe
from qhfi.data.lake import market_store

OUT = Path("config") / "instruments" / "nasdaq100.yaml"

# Sectors for Nasdaq-100 names that are NOT S&P 500 members (so not in sp500.yaml's map).
_NON_SPX_SECTORS = {
    "ARM": "Information Technology", "ASML": "Information Technology",
    "PDD": "Consumer Discretionary", "MELI": "Consumer Discretionary",
    "CCEP": "Consumer Staples", "AZN": "Health Care", "GOOG": "Communication Services",
    # NDX tech names not in the S&P sector map
    "ANSS": "Information Technology", "GFS": "Information Technology",
    "MDB": "Information Technology", "MRVL": "Information Technology",
    "TEAM": "Information Technology", "ZS": "Information Technology",
}

# Fallback list if slickcharts is unreachable (point-in-time; refresh via this script).
_FALLBACK = """AAPL MSFT AMZN NVDA GOOGL GOOG META AVGO TSLA COST NFLX TMUS CSCO PEP ADBE
AMD LIN TXN QCOM INTU AMGN ISRG CMCSA AMAT BKNG HON VRTX ADP PANW GILD ADI SBUX MU MELI
LRCX KLAC REGN INTC SNPS CDNS PYPL MAR ASML ABNB ORLY CSX CRWD FTNT NXPI PCAR CTAS WDAY
MNST ADSK ROP PAYX AEP KDP ODFL CHTR MRVL CPRT ROST DASH FAST KHC EA EXC CCEP VRSK GEHC
CTSH XEL DXCM LULU BIIB ON CSGP ANSS TTD ARM MDB AZN PDD WBD DLTR ZS GFS TEAM CDW""".split()


def fetch_constituents() -> list[str]:
    try:
        r = httpx.get("https://www.slickcharts.com/nasdaq100",
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=20, follow_redirects=True)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        syms = [str(s).replace(".", "-").strip() for s in tables[0]["Symbol"]]
        if len(syms) >= 90:
            print(f"Fetched {len(syms)} constituents from slickcharts")
            return syms
    except Exception as e:  # noqa: BLE001
        print(f"slickcharts unavailable ({type(e).__name__}) → using bundled fallback list")
    return _FALLBACK


def main() -> None:
    tickers = sorted(set(fetch_constituents()))
    spx_sector = {i.id: i.sector for i in load_universe("config/instruments/sp500.yaml").instruments}

    # Keep only names already in the lake (drops delisted/uncached, e.g. ANSS post-acquisition).
    store = market_store()
    def cached(t: str) -> bool:
        return store.has(Instrument(id=t, asset_class=AssetClass.EQUITY))

    dropped = [t for t in tickers if not cached(t)]
    kept = [t for t in tickers if cached(t)]

    instruments, no_sector = [], []
    for t in kept:
        sector = spx_sector.get(t) or _NON_SPX_SECTORS.get(t)
        if not sector:
            no_sector.append(t)
        instruments.append(Instrument(
            id=t, asset_class=AssetClass.EQUITY,
            equity=EquityMeta(gics_sector=sector) if sector else None,
        ))
    uni = Universe(name="nasdaq100", instruments=instruments)
    save_universe(uni, OUT)

    print(f"→ wrote {OUT} with {len(instruments)} names (all cached/servable)")
    print(f"  dropped (uncached/delisted): {dropped or 'none'}")
    print(f"  no GICS sector: {no_sector or 'none'}")
    sectors = {i.sector for i in instruments if i.sector}
    print(f"  sectors covered: {len(sectors)}")


if __name__ == "__main__":
    main()
