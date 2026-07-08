"""Find real DBnomics series codes for the national statistical agencies the user wants, and
confirm which providers exist on DBnomics. Grounds the international macro wiring."""

from __future__ import annotations

import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx

c = httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "qhfi-research"})

WANT = ["BEA", "BLS", "FRED", "Eurostat", "ONS", "NBS", "BOJ", "RBA"]
QUERIES = {
    "BLS": "unemployment rate", "BEA": "gross domestic product", "Eurostat": "unemployment rate",
    "ONS": "gross domestic product", "BOJ": "call rate", "RBA": "cash rate target", "NBS": "consumer price",
}


def main() -> None:
    # 1. which requested providers exist on DBnomics
    try:
        docs = c.get("https://api.db.nomics.world/v22/providers").json()["providers"]["docs"]
        codes = {d["code"] for d in docs}
        print("On DBnomics:", {w: (w in codes) for w in WANT})
        print("  (Census, Treasury → own APIs, not DBnomics)\n")
    except Exception as e:  # noqa: BLE001
        print("providers:", type(e).__name__, e)

    # 2. a flagship series code per provider
    print("Flagship series codes found:")
    for prov, q in QUERIES.items():
        try:
            r = c.get("https://api.db.nomics.world/v22/search", params={"q": q, "limit": "50"})
            r.raise_for_status()
            res = r.json()["results"]["docs"]
            m = next((d for d in res if d.get("provider_code") == prov), None)
            if m:
                code = f"{m['provider_code']}/{m['dataset_code']}/{m['series_code']}"
                print(f"  {prov:<9} {code}  | {m.get('series_name','')[:55]}")
            else:
                print(f"  {prov:<9} no match in top results (providers: {sorted({d.get('provider_code') for d in res})[:6]})")
        except Exception as e:  # noqa: BLE001
            print(f"  {prov:<9} ERROR {type(e).__name__}: {str(e)[:60]}")
        time.sleep(1.0)   # be gentle on DBnomics


if __name__ == "__main__":
    main()
