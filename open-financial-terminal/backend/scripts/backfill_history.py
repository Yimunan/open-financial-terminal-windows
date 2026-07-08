"""One-off: deepen the OFT lake so Barra risk attribution can cover more names.

The Barra `resid_vol` style factor needs ~315 trading days (beta-252 → resid_vol-63), so names with
thin history get dropped as "no model coverage". `DataManager.update` only extends the forward tail
unless `force=True`, so short names stay short. This force-refetches a wide window for any
equity_sectors-pool name (∪ current holdings) that has < MIN_ROWS rows — leaving already-deep names
untouched. Run from the backend venv:  python scripts/backfill_history.py
"""

from __future__ import annotations

from datetime import date, timedelta

from qhfi.core.types import DateRange, Universe

from app.deps import get_data_manager, get_store, make_instrument
from app.services.universe import get_universe

MIN_ROWS = 400            # comfortably over the ~315-day coverage floor
YEARS = 5                 # force-fetch this much history for thin names


def main() -> None:
    dm = get_data_manager()
    names = set(get_universe("equity_sectors").ids)
    for h in get_store().list_holdings():
        if (h.get("asset") or "equity") == "equity":
            names.add(h["symbol"].upper())

    span = DateRange(start=date.today() - timedelta(days=365 * YEARS), end=date.today())
    thin = []
    for sym in sorted(names):
        ins = make_instrument(sym, "equity")
        try:
            cov = dm.coverage(ins)
            rows = cov[2] if cov else 0
        except Exception:  # noqa: BLE001 - corrupt/partial parquet → treat as empty, force-refetch repairs it
            rows = 0
        if rows < MIN_ROWS:
            thin.append(sym)
    print(f"{len(names)} names; {len(thin)} thin (<{MIN_ROWS} rows): {thin}", flush=True)

    fixed, failed = 0, []
    for i, sym in enumerate(thin, 1):
        ins = make_instrument(sym, "equity")
        try:
            dm.update(Universe(name=f"_bf_{sym}", instruments=[ins]), span, force=True)
            cov = dm.coverage(ins)
            rows = cov[2] if cov else 0
            ok = "OK" if rows >= MIN_ROWS else "STILL THIN"
            if rows >= MIN_ROWS:
                fixed += 1
            else:
                failed.append((sym, rows))
            print(f"[{i}/{len(thin)}] {sym}: {rows} rows {ok}", flush=True)
        except Exception as e:  # noqa: BLE001
            failed.append((sym, str(e)[:40]))
            print(f"[{i}/{len(thin)}] {sym}: ERROR {e}", flush=True)

    print(f"\nDONE: deepened {fixed}/{len(thin)}; remaining thin: {failed}", flush=True)


if __name__ == "__main__":
    main()
