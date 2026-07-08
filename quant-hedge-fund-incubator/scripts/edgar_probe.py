"""Probe SEC EDGAR: ticker→CIK, recent 10-Q filings (original reports), and XBRL structured
facts with filing dates (the point-in-time extraction layer). Validates the pipeline source."""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx

HEADERS = {"User-Agent": "qhfi-research contact@example.com"}
TICKER = sys.argv[1] if len(sys.argv) > 1 else "AAPL"


def main() -> None:
    c = httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)

    # 1. ticker -> CIK
    tk = c.get("https://www.sec.gov/files/company_tickers.json").json()
    cik = next(v["cik_str"] for v in tk.values() if v["ticker"] == TICKER)
    cik10 = f"{cik:010d}"
    print(f"{TICKER} -> CIK {cik}")

    # 2. recent 10-Q filings (the original quarterly reports)
    sub = c.get(f"https://data.sec.gov/submissions/CIK{cik10}.json").json()
    r = sub["filings"]["recent"]
    print("\nRecent 10-Q filings (original reports):")
    shown = 0
    for i in range(len(r["form"])):
        if r["form"][i] == "10-Q" and shown < 4:
            acc = r["accessionNumber"][i].replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{r['primaryDocument'][i]}"
            print(f"  filed {r['filingDate'][i]}  {r['accessionNumber'][i]}")
            print(f"     {url}")
            shown += 1

    # 3. XBRL structured facts WITH filing dates (PIT extraction)
    cf = c.get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/us-gaap/NetIncomeLoss.json").json()
    usd = [f for f in cf["units"]["USD"] if f.get("form") == "10-Q"]
    label = cf.get("label", "NetIncomeLoss")
    print(f"\nXBRL us-gaap:NetIncomeLoss ({label}) — last 4 quarterly facts:")
    for f in usd[-4:]:
        val = f["val"] / 1e9
        print(f"  {f.get('fy')}-{f.get('fp')}  period {f['start']}..{f['end']}  "
              f"val ${val:.2f}B  FILED {f['filed']}")
    print("\n→ the FILED date is what makes this point-in-time correct.")


if __name__ == "__main__":
    main()
