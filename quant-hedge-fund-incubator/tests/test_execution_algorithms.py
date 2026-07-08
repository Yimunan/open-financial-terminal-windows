"""Execution scheduling: TWAP even split, POV participation cap, sqrt market impact."""

from __future__ import annotations

import math

import pytest

from qhfi.execution.algorithms import (
    POV,
    TWAP,
    MarketImpactModel,
    expected_cost_bps,
)
from qhfi.execution.base import Order, OrderSide


def _order(qty: float, side: OrderSide = OrderSide.BUY) -> Order:
    return Order(instrument_id="AAPL", side=side, quantity=qty)


# ── market impact ───────────────────────────────────────────────────────────
def test_impact_is_sqrt_in_participation():
    m = MarketImpactModel(eta=10.0)
    assert m.impact_bps(100, adv=10_000) == pytest.approx(10.0 * math.sqrt(0.01))
    # 4× the size → 2× the impact (sqrt law)
    assert m.impact_bps(400, 10_000) == pytest.approx(2 * m.impact_bps(100, 10_000))


def test_impact_zero_adv_is_safe():
    m = MarketImpactModel()
    assert m.impact_bps(100, adv=0.0) == 0.0
    assert m.impact_cost(100, 0.0, price=200.0) == 0.0


# ── TWAP ──────────────────────────────────────────────────────────────────────
def test_twap_splits_evenly_and_conserves_quantity():
    sched = TWAP(horizon_days=5).schedule(_order(1000), adv=100_000, price=200.0)
    assert len(sched) == 5
    assert all(s.quantity == pytest.approx(200.0) for s in sched)
    assert sum(s.quantity for s in sched) == pytest.approx(1000.0)
    assert all(s.side is OrderSide.BUY for s in sched)


def test_twap_preserves_side():
    sched = TWAP(horizon_days=3).schedule(_order(900, OrderSide.SELL), adv=100_000, price=50.0)
    assert all(s.side is OrderSide.SELL for s in sched)
    assert sum(s.quantity for s in sched) == pytest.approx(900.0)


# ── POV ─────────────────────────────────────────────────────────────────────
def test_pov_caps_daily_participation_and_sets_horizon():
    # 5000 shares, ADV 10k, 10% cap → 1000/day → 5 days
    sched = POV(rate=0.10).schedule(_order(5000), adv=10_000, price=100.0)
    assert len(sched) == 5
    assert all(s.participation <= 0.10 + 1e-9 for s in sched)
    assert sum(s.quantity for s in sched) == pytest.approx(5000.0)


def test_pov_horizon_is_ceil_qty_over_rate_adv():
    sched = POV(rate=0.10).schedule(_order(5500), adv=10_000, price=100.0)
    assert len(sched) == math.ceil(5500 / (0.10 * 10_000))  # 6 days


def test_pov_respects_max_days_and_conserves_quantity():
    sched = POV(rate=0.01, max_days=10).schedule(_order(1_000_000), adv=10_000, price=100.0)
    assert len(sched) == 10                                   # would be 100 days uncapped
    assert sum(s.quantity for s in sched) == pytest.approx(1_000_000.0)


def test_pov_zero_adv_executes_in_one_slice():
    sched = POV().schedule(_order(500), adv=0.0, price=10.0)
    assert len(sched) == 1 and sched[0].quantity == pytest.approx(500.0)


def test_expected_cost_bps_is_quantity_weighted():
    sched = TWAP(horizon_days=4).schedule(_order(800), adv=100_000, price=20.0)
    assert expected_cost_bps(sched) == pytest.approx(sched[0].impact_bps)  # equal slices
    assert expected_cost_bps([]) == 0.0
