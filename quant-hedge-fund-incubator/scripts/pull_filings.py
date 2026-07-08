"""Pull original SEC filings (10-Q / 10-K) for a universe into the `filings` lake category.
Bounded to the most recent N per form to keep disk/time reasonable.

  .venv\\Scripts\\python.exe scripts\\pull_filings.py [pool.yaml]
  set SEC_USER_AGENT="Your Name your@email.com"  (SEC fair-access policy)
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.universe_io import load_universe
from qhfi.data.filings import FilingsStore
from qhfi.data.lake import lake_root
from qhfi.data.providers.edgar import EdgarClient

POOL = sys.argv[1] if len(sys.argv) > 1 else "config/instruments/equity_sectors.yaml"
N_10Q = 4     # most recent quarterly reports per company
N_10K = 1     # most recent annual report


def main() -> None:
    universe = load_universe(POOL)
    edgar = EdgarClient()
    store = FilingsStore(lake_root())
    print(f"Pulling filings for {len(universe.instruments)} names → {store.data_dir.resolve()}")
    print(f"(last {N_10Q}× 10-Q + {N_10K}× 10-K each; rate-limited; UA={edgar._http.headers.get('User-Agent')})\n")

    saved = cached = errors = 0
    for i, ins in enumerate(universe.instruments, 1):
        try:
            cik = edgar.ticker_to_cik(ins.id)
            filings = edgar.list_filings(cik, forms=("10-Q", "10-K"))
            want = [f for f in filings if f.form == "10-Q"][:N_10Q] + \
                   [f for f in filings if f.form == "10-K"][:N_10K]
            for f in want:
                if store.has(ins.id, f.accession):
                    cached += 1
                    continue
                store.save(ins.id, f, edgar.fetch_document(f))
                saved += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            if errors <= 12:
                print(f"  {ins.id}: {type(e).__name__} {str(e)[:80]}")
        if i % 25 == 0:
            print(f"  [{i}/{len(universe.instruments)}] saved={saved} cached={cached} errors={errors}", flush=True)

    print(f"\nDONE: {saved} filings saved · {cached} already cached · {errors} errors")
    m = store.manifest()
    if len(m):
        print(f"\nCorpus: {len(m)} filings, {m['bytes'].sum()/1e6:.0f} MB, "
              f"{m['ticker'].nunique()} companies")
        print(m["form"].value_counts().to_string())
        print(f"\nDate range: {m['filing_date'].min()} → {m['filing_date'].max()}")
        print("\nSample (5 most recent):")
        print(m.sort_values("filing_date", ascending=False).head(5)
              [["ticker", "form", "filing_date", "report_date", "accession"]].to_string(index=False))


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
