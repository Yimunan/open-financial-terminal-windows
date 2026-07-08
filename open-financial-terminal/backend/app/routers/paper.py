"""Paper trading — account, positions, orders against the selected broker.

Two books can run in parallel (see deps): the *primary* broker — qhfi's real AlpacaPaperBroker when
Alpaca keys are configured, else the local SimBroker — and an always-available local SimBroker
*sandbox*. Read/trade endpoints take a ``book`` query param ('primary' default | 'sim'); the
sim-only ops (close/flatten/rebalance/reset/cancel/preview) always target the sim sandbox so they
stay usable even while Alpaca is primary. Orders are submitted ONLY on the explicit POST /orders
call; nothing trades automatically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from qhfi.execution.base import Order, OrderSide

from app.deps import (
    alpaca_active,
    broker_kind,
    get_broker,
    get_data_manager,
    get_sim_broker,
    get_store,
    resolve_broker,
)
from app.services import market as mkt
from app.services.broker import SimBroker

router = APIRouter(prefix="/api/paper", tags=["paper"])

_MAX_ACCOUNTS = 20  # sanity cap on the number of sim books


def book_broker(book: str = "primary"):
    """FastAPI dependency: resolve the requested book to a broker (exposes ?book=primary|sim[:id])."""
    return resolve_broker(book)


def sim_account_broker(account: int = 1):
    """FastAPI dependency for sim-only endpoints: resolve ?account=<id> to that account's SimBroker."""
    return get_sim_broker(account)


def _acct_id(broker) -> int:
    """The sim account id a broker addresses (1 for Alpaca/primary, which has no local book)."""
    return getattr(broker, "account_id", 1)

# Realism knobs (sim-only) persisted in the terminal config table. Kept in a sane bps range so a
# fat-fingered value can't make fills absurd; defaults are 0 (frictionless) to preserve prior behavior.
_BPS_MAX = 500.0  # 5%


def _get_bps(store, key: str) -> float:
    try:
        return max(0.0, min(_BPS_MAX, float(store.get_config(key, "0") or 0)))
    except (TypeError, ValueError):
        return 0.0


def _set_bps(store, key: str, value: float) -> float:
    v = max(0.0, min(_BPS_MAX, float(value)))
    store.set_config(key, str(v))
    return v


class OrderIn(BaseModel):
    symbol: str
    asset: str = "equity"
    side: str          # buy | sell
    quantity: float
    type: str = "market"  # market | limit | stop | stop_limit | trailing_stop
    limit_price: float | None = None
    stop_price: float | None = None   # stop / stop_limit trigger price
    trail_pct: float | None = None    # trailing_stop trail distance, percent


class CloseIn(BaseModel):
    symbol: str
    asset: str = "equity"


def _exposure(positions: list[dict], equity: float) -> dict:
    """Gross/net/long/short exposure, concentration (HHI), and asset-class breakdown of the book.

    market_value is signed (negative for shorts). Weights are taken against gross so concentration
    is leverage-independent; gross_pct/net_pct are against equity to show leverage vs the account.
    """
    gross = sum(abs(p["market_value"]) for p in positions)
    net = sum(p["market_value"] for p in positions)
    long_mv = sum(p["market_value"] for p in positions if p["quantity"] > 0)
    short_mv = sum(-p["market_value"] for p in positions if p["quantity"] < 0)  # report as positive
    by_asset: dict[str, float] = {}
    for p in positions:
        by_asset[p["asset"]] = by_asset.get(p["asset"], 0.0) + abs(p["market_value"])
    hhi = sum((abs(p["market_value"]) / gross) ** 2 for p in positions) if gross else 0.0
    largest = max((abs(p["market_value"]) for p in positions), default=0.0)
    return {
        "gross": round(gross, 2),
        "net": round(net, 2),
        "long": round(long_mv, 2),
        "short": round(short_mv, 2),
        "gross_pct": round(gross / equity * 100, 2) if equity else 0.0,
        "net_pct": round(net / equity * 100, 2) if equity else 0.0,
        "long_count": sum(1 for p in positions if p["quantity"] > 0),
        "short_count": sum(1 for p in positions if p["quantity"] < 0),
        "largest_pct": round(largest / gross * 100, 2) if gross else 0.0,  # biggest name as % of gross
        "concentration_hhi": round(hhi, 4),  # 1/N (diversified) … 1.0 (single name)
        "by_asset": {
            a: {"market_value": round(mv, 2), "pct": round(mv / gross * 100, 2) if gross else 0.0}
            for a, mv in sorted(by_asset.items())
        },
    }


class ConfigIn(BaseModel):
    commission_bps: float = 0.0
    slippage_bps: float = 0.0


@router.get("/config")
def config(account: int = 1) -> dict:
    """Broker kind + the selected sim account's realism knobs (commission/slippage bps)."""
    get_sim_broker(account)  # ensure the account row exists (seeds account 1 on first ever call)
    store = get_store()
    acct = store.get_paper_account(account) or {}
    return {
        "broker": broker_kind(),
        "alpaca_active": alpaca_active(),  # when true, a local sim sandbox runs alongside Alpaca
        "commission_bps": round(float(acct.get("commission_bps", 0.0)), 4),
        "slippage_bps": round(float(acct.get("slippage_bps", 0.0)), 4),
    }


@router.post("/config")
def set_config(body: ConfigIn, account: int = 1) -> dict:
    """Persist the selected sim account's realism knobs and drop the cached broker so the new values
    apply to the next fill. No-op for Alpaca (it owns its own fills), but stored on the account."""
    store = get_sim_broker(account).store  # ensures the account exists
    comm = max(0.0, min(_BPS_MAX, float(body.commission_bps)))
    slip = max(0.0, min(_BPS_MAX, float(body.slippage_bps)))
    store.update_paper_account_config(account, commission_bps=comm, slippage_bps=slip)
    get_sim_broker.cache_clear()  # rebuild the sim sandbox(es) with the new bps
    get_broker.cache_clear()  # primary may be that same sim broker
    return {
        "broker": broker_kind(),
        "alpaca_active": alpaca_active(),
        "commission_bps": comm,
        "slippage_bps": slip,
    }


@router.get("/account")
def account(broker=Depends(book_broker)) -> dict:
    acct = broker.get_account()
    dm = get_data_manager()
    positions = []
    for sym, pos in acct.positions.items():
        asset = getattr(broker, "_asset", {}).get(sym, "equity")
        last = None
        if isinstance(broker, SimBroker):
            last = broker.last_price(sym, asset)
        else:
            try:
                _, bars = mkt.fetch_bars(dm, sym, asset)
                last = mkt.quote_from_bars(bars).get("price")
            except Exception:  # noqa: BLE001
                last = pos.avg_price
        last = last or pos.avg_price
        mv = pos.quantity * last
        cost = pos.quantity * pos.avg_price
        positions.append({
            "symbol": sym, "asset": asset, "quantity": round(pos.quantity, 6),
            "avg_price": round(pos.avg_price, 4), "last": round(last, 4),
            "market_value": round(mv, 2), "unrealized_pnl": round(mv - cost, 2),
            "unrealized_pct": round((last / pos.avg_price - 1) * 100, 2) if pos.avg_price else 0.0,
        })
    store = get_store()
    is_sim = isinstance(broker, SimBroker)
    aid = _acct_id(broker)
    exposure = _exposure(positions, acct.equity)
    if is_sim:
        # only the sim book owns the local equity curve; don't pollute it with Alpaca marks.
        # gross/net ride along so /performance can serve an exposure time series. throttled (≥60s).
        store.add_equity_snapshot(
            acct.equity, acct.cash, account_id=aid,
            gross=exposure["gross"], net=exposure["net"],
        )
    return {
        "broker": "sim" if is_sim else broker_kind(),
        "equity": round(acct.equity, 2),
        "cash": round(acct.cash, 2),
        "buying_power": round(acct.cash, 2),
        # realized ledger is sim-owned; Alpaca's realized P&L lives on the Alpaca dashboard
        "realized_pnl": round(store.paper_realized_total(aid), 2) if is_sim else None,
        "exposure": exposure,
        "positions": positions,
    }


def _daily_returns_from_curve(curve: list[dict]):
    """Resample the (intraday) equity snapshots to one point per calendar day (last value) and
    return the daily return series. Used for the calendar-accurate benchmark comparison — the
    per-snapshot series below is fine for scale-robust ratios but not for alignment vs SPY."""
    import pandas as pd

    if len(curve) < 2:
        return pd.Series(dtype="float64")
    s = pd.Series(
        [c["equity"] for c in curve],
        index=pd.to_datetime([c["ts"] for c in curve]),
        dtype="float64",
    )
    daily = s.resample("1D").last().dropna()
    daily.index = daily.index.normalize()
    return daily.pct_change().dropna()


def _benchmark_block(curve: list[dict], symbol: str) -> dict | None:
    """Alpha/beta/capture vs a benchmark, on daily-resampled returns. None when the book spans
    <2 calendar days (single-session sim) or the benchmark series can't be fetched/aligned."""
    import pandas as pd
    from qhfi.evaluation.metrics import benchmark_stats

    rets = _daily_returns_from_curve(curve)
    if len(rets) < 2:
        return None
    try:
        _, bars = mkt.fetch_bars(get_data_manager(), symbol.upper(), "equity")
    except Exception:  # noqa: BLE001 - unknown/halted benchmark symbol
        return None
    if bars is None or bars.empty or "close" not in bars:
        return None
    b = bars["close"].astype(float)
    b.index = pd.to_datetime(b.index).normalize()
    bench_rets = b.pct_change().dropna()
    try:
        stats = benchmark_stats(rets, bench_rets)
    except Exception:  # noqa: BLE001 - degenerate alignment
        return None
    if not stats or stats.get("correlation", 0.0) == 0.0 and stats.get("beta", 0.0) == 0.0:
        return None  # no overlap / no signal
    return {"symbol": symbol.upper(), **{k: round(float(v), 6) for k, v in stats.items()}}


@router.get("/performance")
def performance(broker=Depends(book_broker), benchmark: str = "SPY") -> dict:
    """Equity curve, summary + trade + risk + benchmark metrics, and the realized-P&L ledger.

    Summary/risk metrics come from qhfi.evaluation.metrics over the snapshot return series.
    Snapshots are intraday (≈1/min), so the annualized figures (CAGR/vol/VaR) treat each snapshot
    as one period at the default 252/yr — directional, not a calendar-accurate annualization;
    Sharpe/Sortino/max-DD/trade-stats are scale-robust. The ``benchmark`` block (default SPY) is
    the exception: it is computed on the equity curve *resampled to daily* and aligned to the
    benchmark's daily returns, so it needs ≥2 distinct calendar days (else null). Needs ≥2
    snapshots for the per-period metrics; returns nulls below that. All sim-owned — Alpaca's
    performance lives on its own dashboard.
    """
    import pandas as pd
    from qhfi.evaluation.metrics import cvar, rolling_sharpe, summary, trade_stats, value_at_risk

    store = get_store()
    is_sim = isinstance(broker, SimBroker)
    aid = _acct_id(broker)
    # the equity curve + realized ledger are sim-owned; Alpaca's performance lives on its dashboard
    curve = store.paper_equity_curve(account_id=aid) if is_sim else []
    metrics: dict[str, float | None] = {
        k: None for k in ("cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar")
    }
    risk: dict[str, object] = {"var_95": None, "cvar_95": None, "rolling_sharpe": []}
    if len(curve) >= 2:
        eq = pd.Series([c["equity"] for c in curve], dtype="float64")
        returns = eq.pct_change().dropna()
        if len(returns) >= 2:
            try:
                metrics = {k: round(float(v), 4) for k, v in summary(returns).items()}
                risk["var_95"] = round(float(value_at_risk(returns, 0.05)), 6)
                risk["cvar_95"] = round(float(cvar(returns, 0.05)), 6)
                rs = rolling_sharpe(returns, window=min(20, len(returns)))
                risk["rolling_sharpe"] = [round(float(x), 4) for x in rs.tolist()]
            except Exception:  # noqa: BLE001 - degenerate series (flat/zero-var) → leave nulls
                pass

    closed = [
        {
            "id": o["id"], "ts": o["ts"], "symbol": o["symbol"], "side": o["side"],
            "quantity": o["quantity"], "fill_price": o["fill_price"], "realized_pnl": o["realized_pnl"],
        }
        for o in (store.list_paper_orders(500, aid) if is_sim else [])
        if o.get("realized_pnl") is not None
    ]
    # trade-level analytics over the closed-P&L ledger + a per-symbol net breakdown
    stats = trade_stats([c["realized_pnl"] for c in closed]) if is_sim else None
    by_symbol: dict[str, float] = {}
    for c in closed:
        by_symbol[c["symbol"]] = by_symbol.get(c["symbol"], 0.0) + float(c["realized_pnl"])
    pnl_by_symbol = (
        sorted(({"symbol": s, "realized_pnl": round(v, 2)} for s, v in by_symbol.items()),
               key=lambda r: r["realized_pnl"])
        if is_sim else []
    )
    return {
        "broker": "sim" if is_sim else broker_kind(),
        "equity_curve": [{"ts": c["ts"], "equity": round(c["equity"], 2)} for c in curve],
        "exposure_curve": [
            {"ts": c["ts"], "gross": round(c["gross"], 2), "net": round(c["net"], 2)} for c in curve
        ],
        "metrics": metrics,
        "risk": risk,
        "trade_stats": stats,
        "pnl_by_symbol": pnl_by_symbol,
        "benchmark": _benchmark_block(curve, benchmark) if is_sim else None,
        "realized_total": round(store.paper_realized_total(aid), 2) if is_sim else None,
        "closed_trades": closed,
    }


@router.get("/ops")
def ops(broker=Depends(book_broker)) -> dict:
    """Operational/observability metrics for the sim book: order-status counts, fill rate, and the
    fill-latency distribution (seconds from submit to fill). Sim-owned — Alpaca runs its own order
    lifecycle, so this returns nulls when the primary broker is Alpaca."""
    store = get_store()
    is_sim = isinstance(broker, SimBroker)
    aid = _acct_id(broker)
    if not is_sim:
        return {"broker": broker_kind(), "applicable": False, "counts": {}, "total": 0,
                "fill_rate": None, "latency": None}
    broker.list_orders()  # tick resting orders so any newly-marketable fills are stamped first
    counts = store.paper_order_status_counts(aid)
    filled = counts.get("filled", 0)
    cancelled = counts.get("cancelled", 0)
    total = sum(counts.values())
    decided = filled + cancelled
    lats = sorted(store.paper_fill_latencies(aid))
    latency = None
    if lats:
        n = len(lats)
        latency = {
            "avg_s": round(sum(lats) / n, 3),
            "p50_s": round(lats[n // 2], 3),
            "max_s": round(lats[-1], 3),
            "n": n,
        }
    return {
        "broker": "sim",
        "applicable": True,
        "counts": {"filled": filled, "open": counts.get("open", 0), "cancelled": cancelled},
        "total": total,
        "fill_rate": round(filled / decided, 4) if decided else None,
        "latency": latency,
    }


@router.get("/orders")
def orders(broker=Depends(book_broker)) -> dict:
    return {"orders": broker.list_orders()}  # sim: local rows; alpaca: live get_orders()


def _order_from(body: OrderIn) -> tuple[Order, OrderSide]:
    try:
        side = OrderSide(body.side.lower())
    except ValueError:
        raise HTTPException(400, "side must be 'buy' or 'sell'") from None
    if body.quantity <= 0:
        raise HTTPException(400, "quantity must be positive")
    order = Order(
        instrument_id=body.symbol.upper(), side=side, quantity=body.quantity,
        type=body.type, limit_price=body.limit_price,
    )
    return order, side


class OptionOrderIn(BaseModel):
    occ: str = ""                       # OCC contract id (preferred); else built from the fields below
    underlying: str = ""
    expiry: str = ""                    # ISO YYYY-MM-DD
    strike: float | None = None
    right: str = ""                     # call | put
    side: str                           # buy | sell (to open or close)
    quantity: float                     # number of contracts
    type: str = "market"                # market | limit
    limit_price: float | None = None    # per-contract premium (e.g. 3.50), not ×100
    account: int = 1


@router.post("/option-order")
def option_order(body: OptionOrderIn) -> dict:
    """Single-leg option paper order — always the local sim book (Alpaca/qhfi has no options path).

    Priced off the chain service (per-contract $ = mark × 100), so it flows through the generic
    SimBroker fill/position/P&L machinery; the position shows in the paper book tagged asset 'option'.
    """
    from app.services import options as opt

    occ = body.occ.strip().upper()
    if not occ:
        if not (body.underlying and body.expiry and body.right and body.strike):
            raise HTTPException(400, "provide occ, or underlying+expiry+right+strike")
        occ = opt.occ_symbol(body.underlying, body.expiry, body.right, float(body.strike))
    try:
        side = OrderSide(body.side.lower())
    except ValueError:
        raise HTTPException(400, "side must be 'buy' or 'sell'") from None
    if body.quantity <= 0:
        raise HTTPException(400, "quantity (contracts) must be positive")
    # a per-contract premium limit is compared against the ×100 mark inside the broker
    lp = body.limit_price * 100 if (body.type == "limit" and body.limit_price) else None
    order = Order(instrument_id=occ, side=side, quantity=body.quantity, type=body.type, limit_price=lp)
    broker = get_sim_broker(body.account)
    try:
        oid = broker.submit(order, "option")
    except Exception as e:  # noqa: BLE001 - bad symbol / no price / insufficient buying power
        raise HTTPException(400, str(e)) from None
    return {"order_id": oid, "ok": True, "book": "sim", "occ": occ}


class ComboLegIn(BaseModel):
    occ: str = ""                       # OCC id (preferred); else built from the fields below
    underlying: str = ""
    expiry: str = ""                    # ISO YYYY-MM-DD
    strike: float | None = None
    right: str = ""                     # call | put
    side: str                           # buy | sell (of this leg)
    ratio: int = 1                      # contracts of this leg per 1 spread unit


class ComboOrderIn(BaseModel):
    legs: list[ComboLegIn]              # 2–4 legs (v1: verticals/straddles/etc.)
    quantity: float = 1                 # number of spread units
    account: int = 1                    # market-only in v1 (net-limit fills are v2)


@router.post("/combo-order")
def combo_order(body: ComboOrderIn) -> dict:
    """Multi-leg option paper order (v1: market, per-leg positions) — always the local sim book.

    Every leg is priced off the chain (per-contract $ = mark × 100) and submitted through the
    generic SimBroker fill/position/P&L path, so a spread shows as its individual legs in the
    paper book. All legs are resolved + priced up front (fail fast), the net debit is pre-checked
    against cash, then SELL (credit) legs are submitted before BUY (debit) legs so a debit spread
    fills even when cash < the long leg's cost but ≥ the net debit.
    """
    from app.services import options as opt

    legs = body.legs or []
    if not (2 <= len(legs) <= 4):
        raise HTTPException(400, "a combo needs 2–4 legs")
    if body.quantity <= 0:
        raise HTTPException(400, "quantity (spreads) must be positive")

    # resolve OCC + validate side + price every leg before submitting any
    resolved: list[tuple[str, OrderSide, float, float]] = []  # (occ, side, qty, mark_per_contract)
    for i, lg in enumerate(legs):
        occ = lg.occ.strip().upper()
        if not occ:
            if not (lg.underlying and lg.expiry and lg.right and lg.strike):
                raise HTTPException(400, f"leg {i + 1}: provide occ, or underlying+expiry+right+strike")
            occ = opt.occ_symbol(lg.underlying, lg.expiry, lg.right, float(lg.strike))
        try:
            side = OrderSide(lg.side.lower())
        except ValueError:
            raise HTTPException(400, f"leg {i + 1}: side must be 'buy' or 'sell'") from None
        ratio = int(lg.ratio or 1)
        if ratio <= 0:
            raise HTTPException(400, f"leg {i + 1}: ratio must be positive")
        mark = opt.option_mark(occ)  # per-contract $ (mark × 100)
        if mark is None:
            raise HTTPException(400, f"leg {i + 1}: no price for {occ}")
        resolved.append((occ, side, ratio * body.quantity, mark))

    # net debit (+) / credit (−): Σ sign(buy=+, sell=−) × mark × qty
    net = sum((m if s == OrderSide.BUY else -m) * q for (_o, s, q, m) in resolved)
    broker = get_sim_broker(body.account)
    cash = broker.buying_power()
    if net > cash + 1e-6:
        raise HTTPException(400, f"insufficient buying power: net debit ${net:,.2f}, have ${cash:,.2f}")

    # credits (sells) first, then debits (buys), so the buy legs see the freed-up cash
    order_legs = sorted(resolved, key=lambda r: 0 if r[1] == OrderSide.SELL else 1)
    results = []
    for occ, side, qty, _mark in order_legs:
        order = Order(instrument_id=occ, side=side, quantity=qty, type="market")
        try:
            oid = broker.submit(order, "option")
        except Exception as e:  # noqa: BLE001 - bad symbol / no price / insufficient buying power
            raise HTTPException(400, f"{occ}: {e}") from None
        results.append({"occ": occ, "order_id": oid, "side": side.value, "quantity": qty})

    return {"ok": True, "book": "sim", "net_debit": net, "legs": results}


@router.post("/orders")
def submit(body: OrderIn, broker=Depends(book_broker)) -> dict:
    order, _ = _order_from(body)
    is_sim = isinstance(broker, SimBroker)
    try:
        if is_sim:
            oid = broker.submit(order, body.asset, stop_price=body.stop_price, trail_pct=body.trail_pct)
        else:
            # Alpaca paper handles all 5 order types natively (stop/trail via stop_price/trail_pct)
            oid = broker.submit(order, stop_price=body.stop_price, trail_pct=body.trail_pct)
    except Exception as e:  # noqa: BLE001 - surface broker errors (bad symbol, rejected, etc.)
        raise HTTPException(400, str(e)) from None
    return {"order_id": oid, "ok": True, "book": "sim" if is_sim else "alpaca"}


@router.post("/preview")
def preview(body: OrderIn, broker=Depends(sim_account_broker)) -> dict:
    """Pre-trade check: estimated cost, buying-power verdict, and advisory risk-gate warnings.

    Non-binding — the order still goes through the explicit POST /orders + confirm. Buying power is a
    hard blocker there (longs can't exceed cash); gross/net/concentration are warnings only so the
    paper sandbox stays permissive. Always evaluated against the local sim sandbox (Alpaca runs its
    own pre-trade checks), so it applies even when Alpaca is the primary broker."""
    order, side = _order_from(body)
    px = broker.last_price(body.symbol.upper(), body.asset)
    a = broker.get_account()
    est_price = body.limit_price if (body.type == "limit" and body.limit_price) else px
    est_cost = round(abs(body.quantity) * est_price, 2) if est_price else None
    bp = round(a.cash, 2)
    bp_ok = not (side == OrderSide.BUY and est_cost is not None and est_cost > bp + 1e-6)

    warnings: list[str] = []
    if px is not None:
        gate = _risk_gate_check(a, body, side, px)
        warnings = gate
    return {
        "applicable": True,
        "est_price": round(est_price, 4) if est_price else None,
        "est_cost": est_cost,
        "buying_power": bp,
        "buying_power_ok": bp_ok,
        "warnings": warnings,
    }


def _risk_gate_check(account, body: OrderIn, side: OrderSide, px: float) -> list[str]:
    """Project the order onto the book → post-trade weights → qhfi RiskGate; return warning strings."""
    import pandas as pd
    from qhfi.risk.gates import RiskGate, RiskLimits

    equity = account.equity or 1.0
    signed_now = {s: p.quantity * px for s, p in account.positions.items()}  # rough mark at current px
    sym = body.symbol.upper()
    delta = body.quantity * px * (1.0 if side == OrderSide.BUY else -1.0)
    signed_now[sym] = signed_now.get(sym, 0.0) + delta
    weights = pd.Series({s: v / equity for s, v in signed_now.items() if abs(v) > 1e-9})
    if weights.empty:
        return []
    decision = RiskGate(RiskLimits()).check_weights(weights)
    return [] if decision.approved else [decision.reason]


@router.post("/close")
def close(body: CloseIn, broker=Depends(book_broker)) -> dict:
    """Market-close the entire position in one symbol on the selected book."""
    try:
        oid = broker.close_position(body.symbol)
    except Exception as e:  # noqa: BLE001 - surface broker errors (e.g. buying power on a short cover)
        raise HTTPException(400, str(e)) from None
    if oid is None:
        raise HTTPException(400, f"no open position in {body.symbol.upper()}")
    return {"order_id": oid, "ok": True}


@router.post("/flatten")
def flatten(broker=Depends(book_broker)) -> dict:
    """Market-close every open position on the selected book."""
    ids = broker.flatten_all()
    return {"order_ids": ids, "ok": True, "closed": len(ids)}


# ── Strategy → Paper bridge: deploy a target-weight book as a rebalance ──────────────
class RebalanceIn(BaseModel):
    weights: dict[str, float]      # symbol → target weight (fraction of equity)
    asset: str = "equity"
    gross: float | None = None     # if set, scale weights so Σ|w| = gross (e.g. 1.0 = fully invested)
    execute: bool = False          # False = preview only; True = submit the orders
    min_ticket: float = 1.0        # skip trades smaller than this notional


def _gate_on_weights(weights: dict[str, float]) -> dict:
    """Run qhfi's RiskGate over a target-weight map; advisory (gross/net/per-position)."""
    import pandas as pd
    from qhfi.risk.gates import RiskGate, RiskLimits

    if not weights:
        return {"approved": True, "reason": "ok"}
    decision = RiskGate(RiskLimits()).check_weights(pd.Series(weights))
    return {"approved": decision.approved, "reason": decision.reason}


def _last_price(symbol: str, asset: str) -> float | None:
    """Latest close for a symbol via the shared DataManager (same source as SimBroker.last_price)."""
    try:
        _, bars = mkt.fetch_bars(get_data_manager(), symbol, asset)
    except Exception:  # noqa: BLE001 - unknown/halted symbol
        return None
    return mkt.quote_from_bars(bars).get("price")


def _rebalance_plan(broker, weights: dict, asset: str, gross: float | None, min_ticket: float) -> dict:
    """Diff a target-weight book against current positions → the orders that reach it.

    Works on any Broker (sim or Alpaca) via get_account()/get_positions(). Targets are dollar
    weights of current equity. Symbols held but absent from the target get a zero weight (i.e.
    they're closed). Trades below ``min_ticket`` notional are dropped as noise. Prices and the
    held book use the request's ``asset`` class (rebalance is single-asset-class).
    """
    a = broker.get_account()
    equity = a.equity or 0.0
    w = {s.upper(): float(v) for s, v in weights.items() if v is not None}
    tot = sum(abs(v) for v in w.values())
    if gross and tot > 0:
        scale = gross / tot
        w = {s: v * scale for s, v in w.items()}

    held = {sym: pos.quantity for sym, pos in broker.get_positions().items()}
    # SimBroker exposes last_price (same DataManager source); Alpaca uses the module helper
    broker_px = getattr(broker, "last_price", None)
    orders, skipped = [], []
    for sym in sorted(set(w) | set(held)):
        px = broker_px(sym, asset) if callable(broker_px) else _last_price(sym, asset)
        if px is None or px <= 0:
            skipped.append(sym)
            continue
        target_w = w.get(sym, 0.0)
        cur_qty = held.get(sym, 0.0)
        delta = target_w * equity / px - cur_qty
        notional = delta * px
        if abs(notional) < min_ticket:
            continue
        orders.append({
            "symbol": sym, "asset": asset,
            "side": "buy" if delta > 0 else "sell",
            "quantity": round(abs(delta), 6),
            "price": round(px, 4),
            "notional": round(notional, 2),
            "target_weight": round(target_w, 4),
            "current_weight": round(cur_qty * px / equity, 4) if equity else 0.0,
        })
    return {"equity": round(equity, 2), "orders": orders, "gate": _gate_on_weights(w), "skipped": skipped}


@router.post("/rebalance")
def rebalance(body: RebalanceIn, broker=Depends(book_broker)) -> dict:
    """Deploy a target-weight book into the selected paper book as a market rebalance.

    Two modes: ``execute=false`` returns the proposed orders + advisory risk-gate for a
    confirm-before-send preview (never trades); ``execute=true`` submits them — sells/reduces
    first so freed cash funds the buys, capturing any per-order error (e.g. buying power) without
    aborting the whole rebalance. Consistent with the module's explicit-submit invariant."""
    if not body.weights:
        raise HTTPException(400, "weights required")
    plan = _rebalance_plan(broker, body.weights, body.asset, body.gross, body.min_ticket)
    if not body.execute:
        return {"applicable": True, "executed": False, **plan}

    is_sim = isinstance(broker, SimBroker)
    order_ids, results = [], []
    for o in sorted(plan["orders"], key=lambda o: 0 if o["side"] == "sell" else 1):
        try:
            order = Order(instrument_id=o["symbol"], side=OrderSide(o["side"]), quantity=o["quantity"], type="market")
            oid = broker.submit(order, o["asset"]) if is_sim else broker.submit(order)
            order_ids.append(oid)
            results.append({"symbol": o["symbol"], "ok": True, "order_id": oid})
        except Exception as e:  # noqa: BLE001 - record per-order failure, keep rebalancing the rest
            results.append({"symbol": o["symbol"], "ok": False, "error": str(e)})
    return {"applicable": True, "executed": True, "order_ids": order_ids, "results": results, **plan}


@router.delete("/orders/{order_id}")
def cancel(order_id: str, broker=Depends(book_broker)) -> dict:
    # str id: the sim uses int rowids (coerced), Alpaca uses UUIDs
    return {"ok": broker.cancel(order_id)}


@router.post("/reset")
def reset(broker=Depends(sim_account_broker)) -> dict:
    """Wipe one sim account's book (sim-only — Alpaca has no account-reset API)."""
    broker.reset()
    return {"ok": True}


# ── sim accounts (multi-book CRUD) ───────────────────────────────────────────────
# Each account is an independent local sim book. Account 1 ('Default') always exists and is
# undeletable; the broker layer addresses an account via the `sim:<id>` book token.
class AccountCreateIn(BaseModel):
    name: str
    initial_cash: float = 100_000.0
    commission_bps: float = 0.0
    slippage_bps: float = 0.0


class AccountPatchIn(BaseModel):
    name: str | None = None
    initial_cash: float | None = None
    commission_bps: float | None = None
    slippage_bps: float | None = None


def _account_out(a: dict) -> dict:
    return {
        "id": a["id"], "name": a["name"],
        "cash": round(float(a["cash"]), 2),
        "realized_total": round(float(a["realized_total"]), 2),
        "initial_cash": round(float(a["initial_cash"]), 2),
        "commission_bps": round(float(a["commission_bps"]), 4),
        "slippage_bps": round(float(a["slippage_bps"]), 4),
        "created": a["created"], "archived": bool(a["archived"]),
    }


def _clamp_bps(v: float) -> float:
    return max(0.0, min(_BPS_MAX, float(v)))


def _algos_referencing(store, account_id: int) -> list[str]:
    """Names/ids of armed algos whose book targets this sim account (block delete while armed)."""
    token = f"sim:{account_id}"
    out = []
    for a in store.list_algos():
        if a.get("book") == token and a.get("armed"):
            out.append(a.get("name") or a.get("id") or "algo")
    return out


@router.get("/accounts")
def list_accounts(include_archived: bool = False) -> dict:
    get_sim_broker(1)  # guarantee the Default account exists for the UI before any sim interaction
    store = get_store()
    return {"accounts": [_account_out(a) for a in store.list_paper_accounts(include_archived)]}


@router.post("/accounts")
def create_account(body: AccountCreateIn) -> dict:
    store = get_store()
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "account name required")
    if store.count_paper_accounts(include_archived=True) >= _MAX_ACCOUNTS:
        raise HTTPException(400, f"account limit reached (max {_MAX_ACCOUNTS})")
    if body.initial_cash <= 0:
        raise HTTPException(400, "initial_cash must be positive")
    aid = store.create_paper_account(
        name, float(body.initial_cash), _clamp_bps(body.commission_bps), _clamp_bps(body.slippage_bps)
    )
    return {"ok": True, "account": _account_out(store.get_paper_account(aid))}


@router.patch("/accounts/{account_id}")
def patch_account(account_id: int, body: AccountPatchIn) -> dict:
    store = get_store()
    if store.get_paper_account(account_id) is None:
        raise HTTPException(404, "no such account")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "account name cannot be empty")
        store.rename_paper_account(account_id, name)
    if body.initial_cash is not None and body.initial_cash <= 0:
        raise HTTPException(400, "initial_cash must be positive")
    store.update_paper_account_config(
        account_id,
        initial_cash=float(body.initial_cash) if body.initial_cash is not None else None,
        commission_bps=_clamp_bps(body.commission_bps) if body.commission_bps is not None else None,
        slippage_bps=_clamp_bps(body.slippage_bps) if body.slippage_bps is not None else None,
    )
    get_sim_broker.cache_clear()  # bps/initial-cash changed → rebuild the sim sandbox(es)
    get_broker.cache_clear()
    return {"ok": True, "account": _account_out(store.get_paper_account(account_id))}


@router.post("/accounts/{account_id}/reset")
def reset_account(account_id: int) -> dict:
    """Reset one account to its initial cash and wipe its book (positions/orders/equity)."""
    broker = get_sim_broker(account_id)
    broker.reset()
    return {"ok": True, "account": _account_out(get_store().get_paper_account(account_id))}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int) -> dict:
    store = get_store()
    if account_id == 1:
        raise HTTPException(400, "the Default account cannot be deleted")
    if store.get_paper_account(account_id) is None:
        raise HTTPException(404, "no such account")
    if store.count_paper_accounts() <= 1:
        raise HTTPException(400, "cannot delete the last account")
    armed = _algos_referencing(store, account_id)
    if armed:
        raise HTTPException(400, f"disarm algos targeting this account first: {', '.join(armed)}")
    store.delete_paper_account(account_id)
    get_sim_broker.cache_clear()  # drop any cached broker bound to the deleted account
    get_broker.cache_clear()
    return {"ok": True}
