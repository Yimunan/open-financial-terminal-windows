"""Always-on algo trading runner — fires strategies on a schedule and routes their live
signals to the paper broker.

This is the bridge the terminal was missing: StrategyLab/Backtest tell you what *would* have
happened; the runner turns a strategy's *current* signal into orders against the same paper
broker the Paper Trading widget uses (local SimBroker or qhfi's AlpacaPaperBroker).

One unified cycle drives two algo kinds, both reduced to a target-weight dict and reconciled by
``qhfi.trading.reconcile.diff_to_orders`` — the exact contract the backtest uses, so paper and
backtest can't drift:

* **template**  — a single-symbol StrategyLab template (SMA/EMA cross, RSI, MACD, …). The
  template's per-bar position array's *last element* is the live signal; target weight =
  ``signal * size_pct`` for that one name.
* **xsection**  — a cross-sectional factor book (the same factor catalog + weight builder the
  Backtest widget uses). The *latest* weight row is today's target across the universe.

The background tick loop wakes every ``TICK_SECONDS`` and runs each armed, due algo in a thread
(network + pandas + sqlite are blocking). A global ``paused`` flag is the kill switch; a per-algo
drawdown gate disarms a book that breaches its limit. State lives in the terminal SQLite so armed
algos survive a restart.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from qhfi.execution.base import Order
from qhfi.risk.gates import RiskGate, RiskLimits
from qhfi.trading.reconcile import diff_to_orders

log = logging.getLogger("oft.algo_runner")

_PAUSED_KEY = "algo_runner_paused"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _risk_limits(algo: dict) -> RiskLimits:
    """Kind-aware default gates, overlaid with any per-algo overrides.

    A single-symbol ``template`` book is *meant* to hold one full-size name, so the diversified
    ``max_position`` of a cross-sectional book (0.20) would reject every meaningful trade — its
    base allows a full name. ``xsection`` keeps the diversified defaults. Either can be tightened
    per algo via ``risk``.
    """
    if algo.get("kind", "template") == "template":
        base = RiskLimits(max_gross=1.0, max_net=1.0, max_position=1.0, max_drawdown_kill=0.20)
    else:
        base = RiskLimits()
    r = algo.get("risk") or {}

    def pick(key: str, fallback: float) -> float:
        v = r.get(key)
        return float(v) if v is not None else fallback  # None / missing → kind-aware base

    return RiskLimits(
        max_gross=pick("max_gross", base.max_gross),
        max_net=pick("max_net", base.max_net),
        max_position=pick("max_position", base.max_position),
        max_drawdown_kill=pick("max_drawdown_kill", base.max_drawdown_kill),
    )


class AlgoRunner:
    """Singleton scheduler + cycle executor. Constructed once via ``deps.get_runner()``."""

    TICK_SECONDS = 30

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._paused = False
        self._running: set[str] = set()  # algo ids with a cycle in flight (no double-fire)

    # ── lifecycle (started/stopped from the FastAPI lifespan) ──────────────────
    def start(self) -> None:
        from app.deps import get_store

        self._paused = (get_store().get_config(_PAUSED_KEY, "0") or "0") == "1"
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._tick_loop())
            log.info("algo runner started (paused=%s)", self._paused)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - best-effort teardown
                pass
            self._task = None

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, value: bool) -> None:
        from app.deps import get_store

        self._paused = bool(value)
        get_store().set_config(_PAUSED_KEY, "1" if value else "0")

    def status(self) -> dict:
        from app.deps import alpaca_active, broker_kind, get_store

        algos = get_store().list_algos()
        return {
            "paused": self._paused,
            "broker": broker_kind(),  # the primary; individual algos may target the sim sandbox
            "alpaca_active": alpaca_active(),  # when true, algos can pick primary vs sim book
            "running": self._task is not None and not self._task.done(),
            "armed_count": sum(1 for a in algos if a.get("armed")),
            "algo_count": len(algos),
        }

    # ── scheduling ─────────────────────────────────────────────────────────────
    def _due(self, algo: dict, now: datetime) -> bool:
        cadence = algo.get("cadence") or {}
        last = _parse(algo.get("last_run"))
        if cadence.get("kind") == "interval":
            secs = max(10, int(cadence.get("seconds", 300)))
            return last is None or (now - last).total_seconds() >= secs
        return self._daily_due(cadence, last, now)

    @staticmethod
    def _daily_due(cadence: dict, last: datetime | None, now: datetime) -> bool:
        """Daily cadence: at most one cycle per local trading day, and only after the close time.

        Default ``16:10`` America/New_York — daily EOD bars are only final after the US equity
        close, so gating to just after it keeps the signal off a half-formed same-day bar. ``at``
        (HH:MM) and ``tz`` are configurable; a bad value falls back to once-per-UTC-day so the algo
        still runs rather than silently stalling. pandas does the tz math (bundles its own tz data,
        unlike stdlib zoneinfo on Windows).
        """
        tz = cadence.get("tz") or "America/New_York"
        at = str(cadence.get("at") or "16:10")
        try:
            hh, mm = (int(x) for x in at.split(":", 1))
            now_local = pd.Timestamp(now).tz_convert(tz)
            if last is not None and pd.Timestamp(last).tz_convert(tz).date() >= now_local.date():
                return False  # already ran today (local)
            return (now_local.hour, now_local.minute) >= (hh, mm)
        except Exception:  # noqa: BLE001 - bad tz/time → permissive once-per-UTC-day fallback
            return last is None or last.date() < now.date()

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad tick must never kill the loop
                log.exception("algo runner tick failed")
            await asyncio.sleep(self.TICK_SECONDS)

    async def _tick(self) -> None:
        if self._paused:
            return
        from app.deps import get_store

        now = _now()
        loop = asyncio.get_event_loop()
        for algo in get_store().list_algos():
            aid = algo.get("id")
            if not aid or not algo.get("armed") or aid in self._running:
                continue
            if not self._due(algo, now):
                continue
            self._running.add(aid)
            # blocking cycle (network/pandas/sqlite) off the event loop; fire-and-forget
            loop.run_in_executor(None, self._guarded_cycle, aid)

    def _guarded_cycle(self, algo_id: str) -> None:
        try:
            self.run_cycle(algo_id)
        except Exception:  # noqa: BLE001 - already logged inside; protect the executor thread
            log.exception("algo cycle crashed: %s", algo_id)
        finally:
            self._running.discard(algo_id)

    # ── public sync entry points (called from the router thread too) ────────────
    def run_cycle(self, algo_id: str) -> dict:
        """Run one full cycle for a stored algo: compute signal → reconcile → submit → log."""
        from app.deps import get_store

        store = get_store()
        algo = store.get_algo(algo_id)
        if algo is None:
            raise ValueError(f"unknown algo '{algo_id}'")
        summary = self._execute(algo, submit=True)
        algo["last_run"] = _now().isoformat()
        if summary.get("status") == "killed":
            algo["armed"] = False  # drawdown kill-switch disarms the book
        store.save_algo(algo_id, algo)
        store.add_algo_run(algo_id, summary)
        return summary

    def preview(self, algo: dict) -> dict:
        """Read-only: compute the signal and the orders it *would* submit, without trading."""
        return self._execute(algo, submit=False)

    # ── the unified cycle ───────────────────────────────────────────────────────
    def _execute(self, algo: dict, *, submit: bool) -> dict:
        from app.deps import broker_kind, resolve_broker
        from app.services.broker import SimBroker

        # which book this algo trades: 'sim' → the local sandbox, else the primary (Alpaca/sim)
        book = algo.get("book", "primary")
        broker = resolve_broker(book)
        is_sim = isinstance(broker, SimBroker)
        account_id = getattr(broker, "account_id", 1)  # which sim book to snapshot/gate
        book_label = "sim" if is_sim else broker_kind()

        kind = algo.get("kind", "template")
        try:
            if kind == "template":
                plan = self._template_plan(algo)
            elif kind == "xsection":
                plan = self._xsection_plan(algo)
            else:
                return {"status": "error", "error": f"unknown kind '{kind}'"}
        except Exception as e:  # noqa: BLE001 - surface data/signal failures as a logged status
            log.warning("algo signal failed (%s): %s", algo.get("id"), e)
            return {"status": "error", "error": str(e)}

        weights: dict[str, float] = plan["weights"]
        prices_map: dict[str, float] = plan["prices"]
        instruments = plan["instruments"]
        asset_map: dict[str, str] = plan["assets"]

        # risk-gate the target weights (gross / net / per-name)
        limits = _risk_limits(algo)
        if weights:
            decision = RiskGate(limits).check_weights(pd.Series(weights))
            if not decision.approved:
                return {
                    "status": "rejected", "reason": decision.reason,
                    "signal": plan.get("signal"), "target_weights": weights,
                    "orders": [], "broker": book_label,
                }

        account = broker.get_account()
        orders = diff_to_orders(weights, account, prices_map, instruments)
        intended = [
            {"symbol": o.instrument_id, "side": o.side.value, "quantity": round(o.quantity, 6),
             "asset": asset_map.get(o.instrument_id, "equity")}
            for o in orders
        ]

        if not submit:
            return {
                "status": "preview", "broker": book_label, "signal": plan.get("signal"),
                "target_weights": weights, "orders": intended, "equity": round(account.equity, 2),
            }

        # The equity curve + drawdown kill-switch are sim-book-only: the store's paper_equity series
        # tracks the local sim book, so snapshotting/gating only applies when this algo trades the
        # sim. Alpaca-book algos keep their P&L on the Alpaca dashboard (no local kill-switch).
        if is_sim:
            # Snapshot equity each cycle so the drawdown curve (and thus the kill-switch) builds even
            # when the runner trades headless — without this, the curve only fills while the UI polls
            # /account, so the kill could never trip on an unattended book. Throttled (≥60s) in store.
            from app.deps import get_store

            get_store().add_equity_snapshot(account.equity, account.cash, account_id=account_id)

            # drawdown kill-switch on this sim account's paper book before any new risk goes on
            dd = self._drawdown_gate(limits, account_id)
            if dd is not None and not dd.approved:
                return {
                    "status": "killed", "reason": dd.reason, "signal": plan.get("signal"),
                    "target_weights": weights, "orders": intended, "submitted": [],
                    "broker": book_label,
                }

        submitted = self._submit_orders(broker, orders, asset_map)
        return {
            "status": "ok", "broker": book_label, "signal": plan.get("signal"),
            "target_weights": weights, "orders": intended, "submitted": submitted,
            "equity": round(account.equity, 2),
        }

    def _submit_orders(self, broker, orders: list[Order], asset_map: dict[str, str]) -> list[dict]:
        """Route each order to the broker, mirroring the paper router's sim/Alpaca branch."""
        from app.services.broker import SimBroker

        out: list[dict] = []
        for o in orders:
            try:
                if isinstance(broker, SimBroker):
                    oid = broker.submit(o, asset_map.get(o.instrument_id, "equity"))
                else:
                    oid = broker.submit(o)
                out.append({"order_id": oid, "symbol": o.instrument_id,
                            "side": o.side.value, "quantity": round(o.quantity, 6)})
            except Exception as e:  # noqa: BLE001 - record + keep going so one bad name isn't fatal
                out.append({"symbol": o.instrument_id, "side": o.side.value, "error": str(e)})
        return out

    def _drawdown_gate(self, limits: RiskLimits, account_id: int = 1):
        from app.deps import get_store

        curve = get_store().paper_equity_curve(account_id=account_id)
        if len(curve) < 2:
            return None
        eq = pd.Series([c["equity"] for c in curve], dtype="float64")
        return RiskGate(limits).check_drawdown(eq)

    # ── template (single-symbol StrategyLab signal) ─────────────────────────────
    def _template_plan(self, algo: dict) -> dict:
        from app.deps import get_data_manager, make_instrument
        from app.services import market as mkt
        from app.services import strategy_lab as lab

        symbol = str(algo["symbol"]).upper()
        asset = algo.get("asset", "equity")
        timeframe = algo.get("timeframe", "1d")
        strategy = algo.get("strategy", "sma_cross")
        params = algo.get("params") or {}
        direction = algo.get("direction", "both")
        size_pct = float(algo.get("size_pct", 1.0))

        tmpl = lab._template(strategy)
        if tmpl is None:
            raise ValueError(f"unknown strategy '{strategy}'")

        dm = get_data_manager()
        if timeframe != "1d":
            _, frame = mkt.fetch_bars_intraday(symbol, asset, timeframe)
        else:
            from datetime import date

            end = date.today()
            start = end.replace(year=end.year - 2)
            _, frame = mkt.fetch_bars(dm, symbol, asset, start, end)
        if frame.empty or len(frame) < 30:
            raise ValueError(f"insufficient data for {symbol} ({timeframe})")

        full = {p.key: params.get(p.key, p.default) for p in tmpl.params}
        raw = tmpl.fn(frame["close"], frame["high"], frame["low"], full)
        # Cast to int before the direction filter, exactly as strategy_lab.simulate() does, so the
        # live signal matches what the backtest would have taken on the same bar.
        desired = lab._desired_with_direction(np.asarray(raw, dtype=int), direction)
        signal = int(desired[-1]) if len(desired) else 0  # live signal = last bar's desired position
        last_price = float(frame["close"].iloc[-1])

        weight = signal * size_pct  # signed target weight for the single name
        weights = {symbol: weight} if abs(weight) > 1e-12 else {symbol: 0.0}
        return {
            "weights": weights,
            "prices": {symbol: last_price},
            "instruments": {symbol: make_instrument(symbol, asset)},
            "assets": {symbol: asset},
            "signal": {
                "kind": "template", "symbol": symbol, "strategy": strategy,
                "signal": signal, "last_price": round(last_price, 6),
                "target_weight": round(weight, 4),
            },
        }

    # ── xsection (cross-sectional factor book) ──────────────────────────────────
    def _xsection_plan(self, algo: dict) -> dict:
        from datetime import date, timedelta

        from qhfi.core.types import DateRange

        from app.deps import (
            get_data_manager,
            get_fundamentals_provider,
            get_fundamentals_store,
        )
        from app.services import backtest as bt
        from app.services import factors as fac
        from app.services.universe import get_universe

        universe_name = algo.get("universe", "dow30")
        factor = algo.get("factor", "momentum")
        mode = algo.get("mode", "long_short")
        top_pct = float(algo.get("top_pct", 0.2))
        gross = float(algo.get("size_pct", 1.0))  # scale the whole book

        if factor not in fac.CATALOG:
            raise ValueError(f"unknown factor '{factor}'")
        universe = get_universe(universe_name)

        end = date.today()
        start = end - timedelta(days=400)  # warm-up so the factor has lookback
        span = DateRange(start=start, end=end)
        dm = get_data_manager()
        dm.update(universe, span)
        prices = dm.get_panel(universe, "close", span)
        if prices.empty or prices.shape[1] < 3:
            raise ValueError(f"insufficient data for universe '{universe_name}'")

        scores = fac.build_signed(
            dm, get_fundamentals_store(), get_fundamentals_provider(), universe, prices, factor
        )
        weights_df = bt._weights_from_scores(scores, mode, top_pct, "M")
        if weights_df.empty:
            raise ValueError("no weights produced")
        last_row = weights_df.iloc[-1].dropna()
        last_px = prices.iloc[-1]

        weights: dict[str, float] = {}
        prices_map: dict[str, float] = {}
        instruments: dict = {}
        assets: dict[str, str] = {}
        for ins in universe.instruments:
            iid = ins.id
            w = float(last_row.get(iid, 0.0)) * gross
            if abs(w) < 1e-9:
                continue
            px = last_px.get(iid)
            if px is None or pd.isna(px):
                continue
            weights[iid] = w
            prices_map[iid] = float(px)
            instruments[iid] = ins
            assets[iid] = ins.asset_class.value

        top = sorted(weights.items(), key=lambda kv: abs(kv[1]), reverse=True)[:12]
        return {
            "weights": weights,
            "prices": prices_map,
            "instruments": instruments,
            "assets": assets,
            "signal": {
                "kind": "xsection", "universe": universe_name, "factor": factor, "mode": mode,
                "n_names": len(weights),
                "top_weights": [{"symbol": s, "weight": round(w, 4)} for s, w in top],
            },
        }
