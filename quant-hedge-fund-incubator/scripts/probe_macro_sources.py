"""Probe additional macro data sources for reachability + breadth (this environment blocks
FRED, so we map what else works): DBnomics (meta-aggregator), World Bank, IMF, OECD."""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx

c = httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": "qhfi-research"})


def probe(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"  {name}: UNREACHABLE ({type(e).__name__})")


def dbnomics_providers():
    r = c.get("https://api.db.nomics.world/v22/providers"); r.raise_for_status()
    docs = r.json()["providers"]["docs"]
    notable = [d["code"] for d in docs if d["code"] in
               {"FRED", "Eurostat", "IMF", "WB", "OECD", "ECB", "BIS", "BLS", "BEA", "WTO"}]
    print(f"  DBnomics: {len(docs)} providers reachable. Notable present: {notable}")


def dbnomics_search():
    r = c.get("https://api.db.nomics.world/v22/search", params={"q": "unemployment rate"})
    r.raise_for_status()
    j = r.json()["results"]
    print(f"  DBnomics search 'unemployment rate': {j['num_found']:,} series across providers")


def worldbank():
    r = c.get("https://api.worldbank.org/v2/country/US/indicator/FP.CPI.TOTL.ZG",
              params={"format": "json", "per_page": "3"})
    r.raise_for_status()
    rows = r.json()[1]
    print("  World Bank US inflation (last 3y):",
          [(x["date"], round(x["value"], 2) if x["value"] else None) for x in rows])


def imf():
    r = c.get("https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/USA")
    r.raise_for_status()
    vals = r.json()["values"]["NGDP_RPCH"]["USA"]
    last = list(vals.items())[-3:]
    print("  IMF US real GDP growth %, last 3y:", last)


def oecd():
    # OECD SDMX-JSON: composite leading indicator, USA
    r = c.get("https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_KEI@DF_KEI,/USA.M.LI...",
              headers={"Accept": "application/vnd.sdmx.data+json"})
    r.raise_for_status()
    print(f"  OECD SDMX: reachable (status {r.status_code})")


def main() -> None:
    print("Probing macro data sources (FRED is blocked in this env):\n")
    probe("DBnomics providers", dbnomics_providers)
    probe("DBnomics search", dbnomics_search)
    probe("World Bank", worldbank)
    probe("IMF", imf)
    probe("OECD", oecd)


if __name__ == "__main__":
    main()
