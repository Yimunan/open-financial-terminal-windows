"""Daily paper-trading loop — the live(-ish) counterpart of the backtest engine.

One cycle (`run_once`), intended to fire once per day after the close via APScheduler/cron:

  1. pull latest daily bars (DataStore + provider gap-fill)
  2. build the close panel and run the strategy → today's target-weight row
  3. blend via the allocator (if running a multi-strategy book)
  4. risk-gate the target weights
  5. reconcile against the broker account → orders
  6. risk-gate + submit orders to the (paper) broker
  7. record the cycle (intended vs filled, equity) in the registry

Because steps 2–4 reuse the exact backtest contracts, paper and backtest cannot drift.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd

from qhfi.core.types import DateRange, Universe
from qhfi.data.base import DataProvider, DataStore
from qhfi.execution.base import Broker
from qhfi.risk.gates import RiskGate
from qhfi.strategy.base import Strategy
from qhfi.trading.reconcile import diff_to_orders

log = logging.getLogger("qhfi.trading.loop")


class PaperLoop:
    def __init__(
        self,
        strategy: Strategy,
        universe: Universe,
        store: DataStore,
        provider: DataProvider | None,
        broker: Broker,
        risk: RiskGate | None = None,
        warmup_days: int = 400,
    ) -> None:
        self.strategy = strategy
        self.universe = universe
        self.store = store
        self.provider = provider
        self.broker = broker
        self.risk = risk or RiskGate()
        self.warmup_days = warmup_days

    def run_once(self, today: date | None = None) -> dict:
        """Execute a single daily cycle (see module docstring). Returns a summary dict
        suitable for logging and registry persistence.

        ``today`` is injectable for deterministic tests; defaults to the system date. The
        cycle is a pure-ish read-modify-submit and never raises on a single bad instrument
        or a broker rejection — those surface as fields in the returned summary so the loop
        keeps running and the rejection is auditable.
        """
        today = today or date.today()
        t0 = time.perf_counter()

        def _finish(summary: dict, *, level: int = logging.INFO) -> dict:
            """Attach cycle timing + order counts, log the summary, and return it."""
            sub = summary.get("submitted", [])
            summary.setdefault("counts", {
                "intended": len(summary.get("orders", [])),
                "submitted_ok": sum(1 for s in sub if "error" not in s),
                "submitted_failed": sum(1 for s in sub if "error" in s),
            })
            summary["timing"] = {"elapsed_s": round(time.perf_counter() - t0, 4)}
            log.log(level, "paper cycle %s: %s", summary.get("status"), {
                "counts": summary["counts"], "timing": summary["timing"],
                "reason": summary.get("reason"),
            })
            return summary

        # 1. pull the latest daily bars (best-effort gap-fill; skip cleanly without a provider)
        if self.provider is not None:
            span = DateRange(start=today - timedelta(days=self.warmup_days), end=today)
            for ins in self.universe.instruments:
                try:
                    bars = self.provider.fetch_daily(ins, span)
                    if len(bars):
                        self.store.save(ins, bars)
                except Exception:  # noqa: BLE001 - provider/network failure shouldn't abort the cycle
                    continue

        # 2. build the close panel and run the strategy → today's target-weight row
        prices = self.store.load_panel(self.universe.instruments, "close")
        if prices is None or prices.empty:
            return _finish(
                {"status": "no_data", "target_weights": {}, "orders": [], "submitted": []},
                level=logging.WARNING,
            )
        weights = self.strategy.generate_weights(prices, self.universe)
        if weights is None or len(weights) == 0:
            return _finish(
                {"status": "no_weights", "target_weights": {}, "orders": [], "submitted": []},
                level=logging.WARNING,
            )

        # 3. (allocator blend omitted — single-strategy book)
        target_row = weights.iloc[-1].dropna()
        target: dict[str, float] = {
            str(k): float(v) for k, v in target_row.items() if abs(float(v)) > 1e-12
        }

        # 4. risk-gate the target weights
        decision = self.risk.check_weights(target_row)
        if not decision.approved:
            return _finish({
                "status": "rejected", "reason": decision.reason,
                "target_weights": target, "orders": [], "submitted": [],
            }, level=logging.WARNING)

        # 5. reconcile against the broker account → orders
        account = self.broker.get_account()
        last_px = prices.iloc[-1]
        prices_map: dict[str, float] = {
            iid: float(last_px[iid])
            for iid in target
            if iid in last_px.index and pd.notna(last_px[iid])
        }
        instruments = {ins.id: ins for ins in self.universe.instruments}
        orders = diff_to_orders(target, account, prices_map, instruments)

        # 6. submit orders to the (paper) broker
        submitted: list[dict] = []
        for o in orders:
            try:
                oid = self.broker.submit(o)
                submitted.append({
                    "id": oid, "instrument_id": o.instrument_id,
                    "side": o.side.value, "quantity": o.quantity,
                })
            except Exception as e:  # noqa: BLE001 - record the rejection; keep submitting the rest
                submitted.append({"instrument_id": o.instrument_id, "error": str(e)})

        # 7. summary (intended vs submitted, equity) for logging/registry persistence
        return _finish({
            "status": "ok",
            "target_weights": target,
            "orders": [
                {"instrument_id": o.instrument_id, "side": o.side.value, "quantity": o.quantity}
                for o in orders
            ],
            "submitted": submitted,
            "equity": account.equity,
        })
