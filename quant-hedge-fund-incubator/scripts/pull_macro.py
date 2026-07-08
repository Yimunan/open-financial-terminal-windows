"""Pull core US macro indicators into the `macro` lake category (FRED → DBnomics fallback).

  .venv\\Scripts\\python.exe scripts\\pull_macro.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.data.lake import lake_root
from qhfi.data.macro import MacroStore
from qhfi.data.providers.macro import MACRO_SERIES, MacroProvider
from qhfi.data.providers.worldbank import WB_COUNTRIES, WB_INDICATORS, WorldBankProvider


def main() -> None:
    store = MacroStore(lake_root())
    print(f"Macro lake → {store.data_dir.resolve()}\n")

    # ── US indicators (FRED via DBnomics) ──
    provider = MacroProvider()
    us_saved = us_empty = 0
    print(f"US indicators ({len(MACRO_SERIES)}):")
    for sid, label in MACRO_SERIES.items():
        s = provider.fetch_series(sid)
        if len(s):
            store.save(sid, s)
            us_saved += 1
            print(f"  {sid:<10} {label:<22} {len(s):>5} obs  {s.index.min().date()}→{s.index.max().date()}  latest={s.iloc[-1]:.2f}")
        else:
            us_empty += 1
            print(f"  {sid:<10} {label:<22} — unavailable")

    # ── Global cross-country (World Bank) ──
    wb = WorldBankProvider()
    wb_saved = wb_empty = 0
    print(f"\nGlobal (World Bank): {len(WB_COUNTRIES)} countries × {len(WB_INDICATORS)} indicators")
    for country in WB_COUNTRIES:
        for short, code in WB_INDICATORS.items():
            try:
                s = wb.fetch(country, code)
            except Exception:  # noqa: BLE001
                s = None
            if s is not None and len(s):
                store.save(f"WB_{country}_{short}", s)
                wb_saved += 1
            else:
                wb_empty += 1

    print(f"\nDONE: US {us_saved}/{us_saved+us_empty} (src={'DBnomics' if provider._fred_dead else 'FRED'})  "
          f"·  World Bank {wb_saved}/{wb_saved+wb_empty}")
    cat = store.catalog()
    print(f"Macro category total: {len(cat)} series on disk")
    # sample: latest GDP growth across countries
    print("\nLatest GDP growth (World Bank, %):")
    for c in WB_COUNTRIES:
        if store.has(f"WB_{c}_gdp_growth"):
            print(f"  {c}: {store.load(f'WB_{c}_gdp_growth').iloc[-1]:+.1f}", end="  ")
    print()


if __name__ == "__main__":
    main()
    from qhfi.data.catalog import refresh
    refresh()  # keep docs/DATA.md + config/data_state.yaml in sync
