"""PaperLoop.run_once — the daily signal→reconcile→submit cycle against a fake broker.

Uses an in-memory store + a stub two-name strategy so the cycle is deterministic and offline:
proves it builds the target row, risk-gates it, reconciles against the account, and submits the
expected BUY/SELL deltas. A second case proves the risk gate blocks an over-gross book.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from qhfi.core.types import AssetClass, Instrument, Panel, TargetWeights, Universe
from qhfi.execution.base import Account, Order, Position
from qhfi.risk.gates import RiskGate, RiskLimits
from qhfi.strategy.base import Strategy
from qhfi.trading.loop import PaperLoop


class _FakeStore:
    """Minimal DataStore stand-in: serves a prebuilt close panel, ignores saves."""

    def __init__(self, panel: Panel) -> None:
        self._panel = panel

    def load_panel(self, instruments, field="close", span=None) -> Panel:  # noqa: ARG002
        return self._panel

    def save(self, instrument, bars) -> None:  # pragma: no cover - provider is None in tests
        pass


class _FakeBroker:
    """Records submitted orders; reports a fixed equity and current positions."""

    def __init__(self, equity: float, positions: dict[str, Position] | None = None) -> None:
        self._equity = equity
        self._positions = positions or {}
        self.submitted: list[Order] = []

    def get_account(self) -> Account:
        return Account(equity=self._equity, cash=self._equity, positions=self._positions)

    def get_positions(self) -> dict[str, Position]:
        return self._positions

    def submit(self, order: Order) -> str:
        self.submitted.append(order)
        return f"oid-{len(self.submitted)}"


class _FixedWeights(Strategy):
    """Stub strategy: returns the same target-weight row on every date (last row is used)."""

    name = "fixed_weights"

    def __init__(self, weights: dict[str, float]) -> None:
        super().__init__()
        self._w = weights

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:  # noqa: ARG002
        row = {iid: self._w.get(iid, 0.0) for iid in prices.columns}
        return pd.DataFrame([row], index=[prices.index[-1]])


def _universe() -> Universe:
    return Universe(
        name="pair",
        instruments=[
            Instrument(id="AAPL", asset_class=AssetClass.EQUITY),
            Instrument(id="MSFT", asset_class=AssetClass.EQUITY),
        ],
    )


def _panel() -> Panel:
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    return pd.DataFrame({"AAPL": [190.0, 200.0], "MSFT": [380.0, 400.0]}, index=idx)


def test_run_once_submits_reconciled_orders() -> None:
    broker = _FakeBroker(equity=100_000.0)  # flat book
    loop = PaperLoop(
        strategy=_FixedWeights({"AAPL": 0.10, "MSFT": -0.05}),
        universe=_universe(),
        store=_FakeStore(_panel()),
        provider=None,
        broker=broker,
    )
    summary = loop.run_once(today=date(2024, 1, 3))

    assert summary["status"] == "ok"
    assert summary["equity"] == 100_000.0
    # instrumentation: timing + order counts on the summary
    assert "elapsed_s" in summary["timing"] and summary["timing"]["elapsed_s"] >= 0
    assert summary["counts"]["intended"] == 2
    assert summary["counts"]["submitted_ok"] == 2 and summary["counts"]["submitted_failed"] == 0
    by_id = {o.instrument_id: o for o in broker.submitted}
    # AAPL: 0.10*100k/200 = 50 shares BUY; MSFT: -0.05*100k/400 = -12.5 → 12 (lot) SELL
    assert by_id["AAPL"].side.value == "buy" and by_id["AAPL"].quantity == 50.0
    assert by_id["MSFT"].side.value == "sell" and by_id["MSFT"].quantity == 12.0


def test_run_once_rejects_over_gross_book() -> None:
    broker = _FakeBroker(equity=100_000.0)
    loop = PaperLoop(
        strategy=_FixedWeights({"AAPL": 1.2, "MSFT": 1.2}),  # gross 2.4 > default max_gross 1.5
        universe=_universe(),
        store=_FakeStore(_panel()),
        provider=None,
        broker=broker,
        risk=RiskGate(RiskLimits()),
    )
    summary = loop.run_once(today=date(2024, 1, 3))

    assert summary["status"] == "rejected"
    assert "gross" in summary["reason"]
    assert broker.submitted == []  # nothing reaches the broker on a gate rejection
    assert summary["counts"]["submitted_ok"] == 0  # instrumentation present on early-exit paths
    assert "elapsed_s" in summary["timing"]
