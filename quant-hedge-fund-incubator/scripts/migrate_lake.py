"""One-off migration: consolidate the ad-hoc per-pool lakes into the taxonomy-partitioned
lake (data/lake/market/<asset_class>/<id>.parquet). Universes become pure views.

Sources → market domain:
  data/equity_pool/<asset_class>/*   data/nasdaq/<asset_class>/*   data/managed/<asset_class>/*

On collision (same ticker in two source lakes — e.g. an S&P name that's also Nasdaq-listed)
keeps the file with more rows. Moves (not copies); old lakes are regenerable + gitignored.

  .venv\\Scripts\\python.exe scripts\\migrate_lake.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from qhfi.data.lake import market_store

SOURCES = ["data/equity_pool", "data/nasdaq", "data/managed"]


def rows(p: Path) -> int:
    try:
        return len(pd.read_parquet(p))
    except Exception:  # noqa: BLE001
        return -1


def main() -> None:
    dest_root = market_store().data_dir          # data/lake/market
    moved = collided_kept = collided_replaced = 0

    for src in SOURCES:
        src_path = Path(src)
        if not src_path.exists():
            continue
        for f in src_path.glob("*/*.parquet"):    # <asset_class>/<id>.parquet
            asset_class = f.parent.name
            target_dir = dest_root / asset_class
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f.name
            if target.exists():
                if rows(f) > rows(target):
                    shutil.move(str(f), str(target))
                    collided_replaced += 1
                else:
                    f.unlink()
                    collided_kept += 1
            else:
                shutil.move(str(f), str(target))
                moved += 1
        # remove now-empty source tree
        shutil.rmtree(src_path, ignore_errors=True)

    files = list(dest_root.glob("*/*.parquet"))
    by_class: dict[str, int] = {}
    for f in files:
        by_class[f.parent.name] = by_class.get(f.parent.name, 0) + 1
    print(f"Migrated → {dest_root.resolve()}")
    print(f"  moved={moved}  collisions(kept-existing)={collided_kept}  collisions(replaced)={collided_replaced}")
    print(f"  total now: {len(files)} files  {by_class}")


if __name__ == "__main__":
    main()
