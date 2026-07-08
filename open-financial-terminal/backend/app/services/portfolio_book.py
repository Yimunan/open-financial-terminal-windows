"""Portfolio book — save named target portfolios (a weight + allocation list) and turn weights
into a concrete dollar/share allocation at current prices.

A *portfolio* here is a named list of target weights ``[{symbol, asset, weight}]`` plus a mode
(``long_only`` | ``long_short``) and free metadata. It is the terminal-owned analogue of a
strategy's ``TargetWeights`` row: where a backtest *derives* weights, this module lets a user
*author and persist* them (seeded e.g. from a backtest's top weights), normalise their gross/net
exposure, and value them into shares against a capital base.

Pure pandas/price-lookup (presentation layer); persistence is the generic registry CRUD on the
store. Weights are fractions (0.05 = 5%); the UI shows percents.
"""

from __future__ import annotations

from typing import Any

from qhfi.data.manager import DataManager

from app.services.market import fetch_bars


# ── persistence (store-backed registry) ───────────────────────────────────────────
def list_portfolios(store: Any, q: str = "") -> dict:
    items = [{**r, "builtin": False} for r in store.list_portfolios()]
    if q:
        ql = q.lower()

        def match(p: dict) -> bool:
            tags = p.get("tags") or []
            tag_str = " ".join(tags) if isinstance(tags, list) else str(tags)
            syms = " ".join(a.get("symbol", "") for a in (p.get("allocations") or []))
            hay = " ".join(str(p.get(k, "")) for k in ("name", "description", "mode", "notes"))
            return ql in (hay + " " + tag_str + " " + syms).lower()

        items = [p for p in items if match(p)]
    return {"portfolios": items}


def save_portfolio(store: Any, name: str, record: dict) -> None:
    allocations = _clean_allocations(record.get("allocations") or [])
    store.save_portfolio(
        name,
        {
            "name": name,
            "description": record.get("description", ""),
            "mode": record.get("mode", "long_short"),
            "allocations": allocations,
            "tags": record.get("tags") or [],
            "notes": record.get("notes", ""),
        },
    )


def remove_portfolio(store: Any, name: str) -> None:
    store.remove_portfolio(name)


def _clean_allocations(raw: list[dict]) -> list[dict]:
    """Keep well-formed {symbol, asset, weight} rows; drop blanks; coerce weight to float."""
    out = []
    for a in raw:
        sym = str(a.get("symbol", "")).strip().upper()
        if not sym:
            continue
        try:
            w = float(a.get("weight", 0) or 0)
        except (TypeError, ValueError):
            w = 0.0
        out.append({"symbol": sym, "asset": (a.get("asset") or "equity"), "weight": w})
    return out


# ── weights → exposures / normalisation ───────────────────────────────────────────
def _exposures(allocations: list[dict]) -> dict:
    longs = [a["weight"] for a in allocations if a["weight"] > 0]
    shorts = [a["weight"] for a in allocations if a["weight"] < 0]
    gross = sum(abs(a["weight"]) for a in allocations)
    net = sum(a["weight"] for a in allocations)
    return {
        "gross": round(gross, 6),
        "net": round(net, 6),
        "long": round(sum(longs), 6),
        "short": round(sum(shorts), 6),
        "n_long": len(longs),
        "n_short": len(shorts),
        "n": len(allocations),
    }


def normalize(allocations: list[dict], mode: str = "long_short") -> dict:
    """Scale weights to gross = 1. ``long_short`` also recentres so net = 0 (dollar-neutral);
    ``long_only`` drops shorts and scales the longs to sum 1."""
    allocs = _clean_allocations(allocations)
    if mode == "long_only":
        allocs = [a for a in allocs if a["weight"] > 0]
        total = sum(a["weight"] for a in allocs)
        if total > 0:
            for a in allocs:
                a["weight"] = round(a["weight"] / total, 6)
    else:  # long_short, dollar-neutral
        n = len(allocs)
        net = sum(a["weight"] for a in allocs)
        if n:
            mean = net / n
            for a in allocs:
                a["weight"] = a["weight"] - mean  # recentre → net 0
        gross = sum(abs(a["weight"]) for a in allocs)
        if gross > 0:
            for a in allocs:
                a["weight"] = round(a["weight"] / gross, 6)
    return {"allocations": allocs, "exposures": _exposures(allocs)}


# ── weights → dollar / share allocation at current prices ──────────────────────────
def allocate(dm: DataManager, allocations: list[dict], capital: float = 100_000.0) -> dict:
    """Value each target weight into a notional and share count at the latest close.

    Returns per-name rows (weight, price, notional, fractional + integer shares) plus the
    invested/leftover cash totals. A symbol whose price can't be fetched is returned with a
    null price and zero shares (so the table still lists it).
    """
    allocs = _clean_allocations(allocations)
    rows, invested = [], 0.0
    for a in allocs:
        try:
            _, bars = fetch_bars(dm, a["symbol"], a.get("asset", "equity"))
            price = float(bars["close"].iloc[-1]) if not bars.empty else None
        except Exception:  # noqa: BLE001 - one unpriceable symbol shouldn't sink the list
            price = None
        notional = a["weight"] * capital
        shares = (notional / price) if price else None
        if price:
            invested += abs(notional)
        rows.append({
            "symbol": a["symbol"],
            "asset": a.get("asset", "equity"),
            "weight": round(a["weight"], 6),
            "price": None if price is None else round(price, 4),
            "notional": round(notional, 2),
            "shares": None if shares is None else round(shares, 4),
            "shares_int": None if shares is None else int(shares),
            "side": "long" if a["weight"] > 0 else ("short" if a["weight"] < 0 else "flat"),
        })
    return {
        "capital": round(capital, 2),
        "rows": rows,
        "exposures": _exposures(allocs),
        "gross_notional": round(sum(abs(r["notional"]) for r in rows), 2),
        "net_notional": round(sum(r["notional"] for r in rows), 2),
        "priced": sum(1 for r in rows if r["price"] is not None),
    }
