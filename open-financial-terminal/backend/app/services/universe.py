"""Universe service — discover and load qhfi universe YAMLs, and search symbols.

v1 symbol search is over the bundled universe definitions (no paid search API). Any symbol
not in a universe is still tradable: the market endpoints accept free-typed tickers.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from qhfi.core.types import Universe
from qhfi.core.universe_io import load_universe

from app.config import get_terminal_settings


def list_universes() -> list[str]:
    d = get_terminal_settings().universe_dir
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


@lru_cache(maxsize=32)
def get_universe(name: str) -> Universe:
    path: Path = get_terminal_settings().universe_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"universe '{name}' not found at {path}")
    return load_universe(path)


def search(query: str, limit: int = 20) -> list[dict]:
    """Match a query against every instrument id in every universe. Returns hits with the
    asset class and originating universe so the UI can fetch with the right provider."""
    q = query.strip().upper()
    if not q:
        return []
    seen: dict[str, dict] = {}
    for uname in list_universes():
        try:
            uni = get_universe(uname)
        except Exception:  # noqa: BLE001 - a malformed yaml shouldn't break search
            continue
        for ins in uni.instruments:
            if q in ins.id.upper() and ins.id not in seen:
                seen[ins.id] = {
                    "symbol": ins.id,
                    "asset": ins.asset_class.value,
                    "sector": ins.sector,
                    "universe": uname,
                }
    # Fold in freshly listed tickers (SEC EDGAR) that aren't in any static universe yet.
    from app.services import listings as ls

    for hit in ls.search_cached(get_terminal_settings().data_dir, q):
        seen.setdefault(hit["symbol"], hit)
    hits = sorted(seen.values(), key=lambda h: (not h["symbol"].startswith(q), h["symbol"]))
    return hits[:limit]
