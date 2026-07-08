"""Pull political & geo-risk SERIES into the lake under geopolitics/risk/ (raw capture).

Two sources, both published metrics captured verbatim (no in-house scoring):
  * GPR index (Caldara-Iacoviello): headline + Threats/Acts + country sub-indices, monthly + daily.
  * GDELT 2.0 DOC API: average-tone and article-volume timelines per geopolitical query.

  .venv\\Scripts\\python.exe scripts\\pull_geopolitics.py
"""

from __future__ import annotations

import sys
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.data.geopolitics import GeoRiskStore
from qhfi.data.lake import lake_root
from qhfi.data.providers.gdelt import QUERIES, GdeltProvider
from qhfi.data.providers.gpr import GprProvider

# GPR headline columns → series ids. Country columns (GPRC_*) are mapped dynamically.
_GPR_HEAD = {"GPR": "gpr_monthly", "GPRT": "gpr_threats", "GPRA": "gpr_acts"}
_GDELT_TOPICS = ["geopolitical_risk", "war", "sanctions", "oil_supply"]   # gentle on the rate limit


def pull_gpr(store: GeoRiskStore) -> int:
    gpr, saved = GprProvider(), 0
    # Monthly: headline + threats/acts + every country sub-index.
    try:
        m = gpr.fetch_monthly()
        for col in m.columns:
            sid = _GPR_HEAD.get(col)
            if sid is None and col.upper().startswith("GPRC_"):
                sid = "gpr_country_" + col.split("_", 1)[1].lower()
            if sid is None:
                continue
            s = gpr.series(m, col)
            if not s.empty:
                store.save(sid, s)
                saved += 1
        print(f"  GPR monthly: saved {saved} series ({len(m)} months, "
              f"{m.index.min().date()}→{m.index.max().date()})")
    except Exception as e:                                  # noqa: BLE001
        print(f"  GPR monthly ERROR {type(e).__name__}: {e}")
    # Daily headline (separate file; best-effort).
    try:
        d = gpr.fetch_daily()
        head = next((c for c in ("GPRD", "GPRD_MA7", "GPR", "GPRD_") if c in d.columns), None)
        s = gpr.series(d, head) if head else None
        if s is not None and not s.empty:
            store.save("gpr_daily", s)
            saved += 1
            print(f"  GPR daily:   saved gpr_daily ({len(s)} days, "
                  f"{s.index.min().date()}→{s.index.max().date()})")
        else:
            print(f"  GPR daily:   no usable headline column (cols={list(d.columns)[:8]}…)")
    except Exception as e:                                  # noqa: BLE001
        print(f"  GPR daily ERROR {type(e).__name__}: {e}")
    return saved


def pull_gdelt(store: GeoRiskStore) -> int:
    gd, saved = GdeltProvider(), 0
    end = date.today()
    start = date(end.year - 3, end.month, 1)               # GDELT DOC covers ~2017→
    for slug in _GDELT_TOPICS:
        q = QUERIES[slug]
        for mode, prefix in (("timelinetone", "gdelt_tone_"), ("timelinevolraw", "gdelt_vol_")):
            try:
                s = gd.fetch_timeline(q, mode=mode, start=start, end=end)
                if s.empty:
                    print(f"  GDELT {mode} {slug}: (empty)")
                    continue
                store.save(prefix + slug, s)
                saved += 1
                print(f"  GDELT {prefix + slug}: {len(s)} obs "
                      f"{s.index.min().date()}→{s.index.max().date()}")
            except Exception as e:                          # noqa: BLE001 — 429s expected
                print(f"  GDELT {mode} {slug} ERROR {type(e).__name__}: {e}")
    return saved


def main() -> None:
    store = GeoRiskStore(lake_root())
    print(f"Geopolitics → {store.data_dir.resolve()}\n")
    print("GPR index (Caldara-Iacoviello):")
    n_gpr = pull_gpr(store)
    print("\nGDELT 2.0 tone/volume timelines:")
    n_gdelt = pull_gdelt(store)

    cat = store.catalog()
    print(f"\nDONE: {n_gpr} GPR + {n_gdelt} GDELT series stored.")
    if not cat.empty:
        print(f"\ncatalog ({len(cat)} series):")
        print(cat.to_string(index=False))
    print("\nRaw capture only — GPR is the authors' published index; GDELT tone is GDELT's own "
          "metric. No in-house sentiment scoring (deliberate later phase).")


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
