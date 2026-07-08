"""Pull insider (Form 3/4/5) transactions for a universe into the `ownership/insider` lake
category. Bounded to the most recent N filings per company to keep disk/time reasonable.

  .venv\\Scripts\\python.exe scripts\\pull_form4.py [pool.yaml]
  set SEC_USER_AGENT="Your Name your@email.com"  (SEC fair-access policy)
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.core.universe_io import load_universe
from qhfi.data.insider import InsiderStore
from qhfi.data.lake import lake_root
from qhfi.data.providers.edgar import EdgarClient
from qhfi.data.providers.form4 import InsiderClient

POOL = sys.argv[1] if len(sys.argv) > 1 else "config/instruments/equity_sectors.yaml"
N_FILINGS = 40   # most recent insider filings per company


def main() -> None:
    universe = load_universe(POOL)
    edgar = EdgarClient()
    insider = InsiderClient(edgar)
    store = InsiderStore(lake_root())
    print(f"Pulling insider filings for {len(universe.instruments)} names → {store.data_dir.resolve()}")
    print(f"(last {N_FILINGS} Form 3/4/5 each; rate-limited; UA={edgar._http.headers.get('User-Agent')})\n")

    saved = cached = errors = 0
    for i, ins in enumerate(universe.instruments, 1):
        try:
            cik = edgar.ticker_to_cik(ins.id)
            for f in insider.list_insider(cik)[:N_FILINGS]:
                if store.has_accession(ins.id, f.accession):
                    cached += 1
                    continue
                txns = insider.fetch_transactions(f)
                if not txns.empty:
                    store.save(ins.id, txns)
                    saved += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            if errors <= 12:
                print(f"  {ins.id}: {type(e).__name__} {str(e)[:80]}")
        if i % 25 == 0:
            print(f"  [{i}/{len(universe.instruments)}] saved={saved} cached={cached} errors={errors}", flush=True)

    print(f"\nDONE: {saved} filings parsed · {cached} already cached · {errors} errors")
    cat = store.catalog()
    if len(cat):
        print(f"\nCorpus: {int(cat['transactions'].sum())} transactions across "
              f"{len(cat)} companies")
        print(cat.sort_values("transactions", ascending=False).head(8).to_string(index=False))


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
