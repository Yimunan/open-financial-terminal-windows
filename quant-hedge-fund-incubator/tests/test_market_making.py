"""Market-making engine tests — matcher fills, inventory control, and a synthetic end-to-end.

Synthetic, offline, deterministic. The synthetic book is the *oracle*: it pins correctness
(accounting identity, fill mechanics, inventory bounds) independent of real-data realism.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.eventdriven.engine import MarketMakingEngine
from qhfi.backtest.eventdriven.data_book import BookReplayDataHandler
from qhfi.backtest.eventdriven.events import BookEvent, QuoteEvent, TradeEvent
from qhfi.backtest.eventdriven.matching import LimitOrderMatchingHandler
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.data.microstructure import book_features, forward_return_on_obi
from qhfi.evaluation.mm_metrics import mm_summary
from qhfi.strategy.library.mm.alpha_quoter import AlphaQuoterMM, AlphaQuoterMMParams
from qhfi.strategy.library.mm.avellaneda_stoikov import ASParams, AvellanedaStoikovMM
from qhfi.strategy.library.mm.linear_inventory import LinearInventoryMM, LinearInventoryMMParams
from qhfi.strategy.library.mm.symmetric import SymmetricMM, SymmetricMMParams
from qhfi.trading.quoting_loop import PaperQuotingLoop, QuotingRiskLimits

SYM = "BTC/USDT"


def _instrument() -> Instrument:
    return Instrument(id=SYM, asset_class=AssetClass.CRYPTO, exchange="okx", lot_size=1e-9)


def _book(ts_ms: int, bid_px, bid_sz, ask_px, ask_sz) -> BookEvent:
    return BookEvent(
        timestamp=pd.Timestamp(ts_ms, unit="ms", tz="UTC"), instrument=SYM,
        bids=((bid_px, bid_sz),), asks=((ask_px, ask_sz),),
        mid=(bid_px + ask_px) / 2.0, microprice=(bid_px + ask_px) / 2.0, obi=0.0,
    )


class _BookViewStub:
    def __init__(self, pos: float) -> None:
        self._pos = pos

    def equity(self) -> float:
        return 100_000.0

    def position(self, instrument: str) -> float:
        return self._pos

    def last_price(self, instrument: str) -> float:
        return 100.0


# ── matcher: deterministic cross fills ────────────────────────────────────────────
def test_cross_fill_on_sweep():
    m = LimitOrderMatchingHandler(cost_model=BpsCostModel(0.0), queue_model=False)
    instr = _instrument()
    m.on_book(_book(0, 99.99, 10, 100.01, 10), instr)        # seed prev_book (mid 100)
    m.post(QuoteEvent(timestamp=pd.Timestamp(0, unit="ms", tz="UTC"), instrument=SYM,
                      bid_px=100.0, ask_px=200.0, bid_size=1.0, ask_size=1.0))
    fills = m.on_book(_book(1, 98.0, 10, 99.0, 10), instr)   # best_ask 99 ≤ our bid 100 → hit
    assert len(fills) == 1
    f = fills[0]
    assert f.delta_units == pytest.approx(1.0) and f.fill_price == pytest.approx(100.0)
    assert f.ref_price == pytest.approx(100.0)               # mid at post (markout reference)
    assert f.commission == 0.0 and f.slippage == 0.0


def test_no_fill_when_not_crossed():
    m = LimitOrderMatchingHandler(cost_model=BpsCostModel(0.0), queue_model=False)
    instr = _instrument()
    m.on_book(_book(0, 99.99, 10, 100.01, 10), instr)
    m.post(QuoteEvent(timestamp=pd.Timestamp(0, unit="ms", tz="UTC"), instrument=SYM,
                      bid_px=100.0, ask_px=200.0, bid_size=1.0, ask_size=1.0))
    fills = m.on_book(_book(1, 100.0, 10, 100.5, 10), instr)  # best_ask 100.5 > bid 100
    assert fills == []


def test_partial_fill_via_trade():
    m = LimitOrderMatchingHandler(cost_model=BpsCostModel(0.0), queue_model=False)
    instr = _instrument()
    m.on_book(_book(0, 99.99, 10, 100.01, 10), instr)
    m.post(QuoteEvent(timestamp=pd.Timestamp(0, unit="ms", tz="UTC"), instrument=SYM,
                      bid_px=100.0, ask_px=200.0, bid_size=5.0, ask_size=5.0))
    t = TradeEvent(timestamp=pd.Timestamp(1, unit="ms", tz="UTC"), instrument=SYM,
                   price=100.0, size=3.0, side="sell")
    fills = m.on_trade(t, instr)
    assert len(fills) == 1 and fills[0].delta_units == pytest.approx(3.0)   # partial: 3 of 5
    # remaining 2 still rest → a larger print fills the rest
    t2 = TradeEvent(timestamp=pd.Timestamp(2, unit="ms", tz="UTC"), instrument=SYM,
                    price=100.0, size=10.0, side="sell")
    fills2 = m.on_trade(t2, instr)
    assert len(fills2) == 1 and fills2[0].delta_units == pytest.approx(2.0)


def test_queue_position_gates_fill():
    m = LimitOrderMatchingHandler(cost_model=BpsCostModel(0.0), queue_model=True)
    instr = _instrument()
    m.on_book(_book(0, 100.0, 5.0, 101.0, 5.0), instr)       # 5 units resting at our bid price
    m.post(QuoteEvent(timestamp=pd.Timestamp(0, unit="ms", tz="UTC"), instrument=SYM,
                      bid_px=100.0, ask_px=200.0, bid_size=2.0, ask_size=2.0))
    # A print smaller than the queue-ahead (5) does not reach us.
    small = TradeEvent(timestamp=pd.Timestamp(1, unit="ms", tz="UTC"), instrument=SYM,
                       price=100.0, size=3.0, side="sell")
    assert m.on_trade(small, instr) == []
    # Cumulative depletion now exceeds the queue → we fill.
    big = TradeEvent(timestamp=pd.Timestamp(2, unit="ms", tz="UTC"), instrument=SYM,
                     price=100.0, size=4.0, side="sell")
    fills = m.on_trade(big, instr)
    assert len(fills) == 1 and fills[0].delta_units == pytest.approx(2.0)


# ── AS strategy: reservation price + spread ───────────────────────────────────────
def test_reservation_price_symmetric_at_zero_inventory():
    p = ASParams(gamma=0.1, kappa=1.5, horizon=1.0, inv_soften=False, join_only=False, obi_alpha=0.0)
    strat = AvellanedaStoikovMM(p)
    ev = _book(0, 99.5, 5, 100.5, 5)                          # mid 100, microprice 100, obi 0
    q = strat.on_book(ev, _BookViewStub(0.0))[0]
    assert (q.bid_px + q.ask_px) / 2.0 == pytest.approx(100.0)   # r == s at q=0
    spread = (2.0 / 0.1) * math.log1p(0.1 / 1.5)                 # σ=0 ⇒ width is the κ term only
    assert (q.ask_px - q.bid_px) == pytest.approx(spread)


def test_inventory_skews_reservation_price_by_formula():
    mids = [100.0, 100.1, 99.9, 100.2, 100.0]
    p = ASParams(gamma=0.1, kappa=1.5, horizon=1.0, inv_soften=False, join_only=False,
                 obi_alpha=0.0, q_max=1e9, sigma_window=100)

    def run(q: float) -> QuoteEvent:
        strat = AvellanedaStoikovMM(p)
        last = None
        for i, m in enumerate(mids):
            last = strat.on_book(_book(i, m - 0.5, 5, m + 0.5, 5), _BookViewStub(q))[0]
        return last

    q0, q5 = run(0.0), run(5.0)
    mid0 = (q0.bid_px + q0.ask_px) / 2.0
    mid5 = (q5.bid_px + q5.ask_px) / 2.0
    sigma = float(np.std(np.diff(np.log(mids)), ddof=0))
    risk = 0.1 * sigma**2 * 1.0
    assert mid0 == pytest.approx(100.0)                          # s at q=0
    assert (mid0 - mid5) == pytest.approx(5.0 * risk)            # r = s − q·γ·σ²·τ


def test_inventory_limit_forces_one_sided_quoting():
    p = ASParams(gamma=0.1, kappa=1.5, q_max=10.0, inv_soften=True, join_only=False)
    strat = AvellanedaStoikovMM(p)
    ev = _book(0, 99.5, 5, 100.5, 5)
    long_q = strat.on_book(ev, _BookViewStub(10.0))[0]
    assert long_q.bid_px is None and long_q.ask_px is not None   # at +limit: ask-only
    short_q = strat.on_book(ev, _BookViewStub(-10.0))[0]
    assert short_q.ask_px is None and short_q.bid_px is not None  # at −limit: bid-only


def test_inventory_soften_widens_before_suppression():
    p = ASParams(gamma=0.1, kappa=1.5, q_max=10.0, inv_soften=True, join_only=False, obi_alpha=0.0)
    strat = AvellanedaStoikovMM(p)
    ev = _book(0, 99.5, 5, 100.5, 5)                             # s = 100, σ = 0
    bid5 = strat.on_book(ev, _BookViewStub(5.0))[0].bid_px
    bid8 = strat.on_book(ev, _BookViewStub(8.0))[0].bid_px
    assert bid8 < bid5                                           # longer ⇒ bid pushed lower (wider)


# ── baseline (SymmetricMM) + scale-free inventory quoter (LinearInventoryMM) ──────
def test_symmetric_mm_one_sided_at_limit():
    strat = SymmetricMM(SymmetricMMParams(half_spread_bps=5.0, q_max=10.0, join_only=False))
    ev = _book(0, 99.5, 5, 100.5, 5)                            # mid 100
    assert strat.on_book(ev, _BookViewStub(10.0))[0].bid_px is None     # +limit → ask-only
    assert strat.on_book(ev, _BookViewStub(-10.0))[0].ask_px is None    # −limit → bid-only
    flat = strat.on_book(ev, _BookViewStub(0.0))[0]
    assert (flat.bid_px + flat.ask_px) / 2.0 == pytest.approx(100.0)    # symmetric about mid


def test_linear_inventory_skews_center_with_inventory():
    p = LinearInventoryMMParams(half_spread_bps=5.0, skew_bps=10.0, obi_alpha=0.0,
                                q_max=100.0, join_only=False)
    strat = LinearInventoryMM(p)
    ev = _book(0, 99.5, 5, 100.5, 5)                            # microprice 100
    q0 = strat.on_book(ev, _BookViewStub(0.0))[0]
    ql = strat.on_book(ev, _BookViewStub(50.0))[0]
    c0 = (q0.bid_px + q0.ask_px) / 2.0
    cl = (ql.bid_px + ql.ask_px) / 2.0
    assert cl < c0                                              # long → center pushed down (sell bias)
    assert (c0 - cl) == pytest.approx(0.5 * 100.0 * 10.0 / 1e4, abs=1e-9)   # lean 0.5 × skew_bps


def test_linear_inventory_obi_tilts_center():
    p = LinearInventoryMMParams(half_spread_bps=5.0, skew_bps=0.0, obi_alpha=0.5,
                                q_max=100.0, join_only=False)
    strat = LinearInventoryMM(p)
    flat = _book(0, 99.5, 5, 100.5, 5)                          # obi 0
    up = BookEvent(timestamp=pd.Timestamp(0, unit="ms", tz="UTC"), instrument=SYM,
                   bids=((99.5, 5.0),), asks=((100.5, 5.0),), mid=100.0, microprice=100.0, obi=0.6)
    qf = strat.on_book(flat, _BookViewStub(0.0))[0]
    qu = strat.on_book(up, _BookViewStub(0.0))[0]
    assert (qu.bid_px + qu.ask_px) / 2.0 > (qf.bid_px + qf.ask_px) / 2.0    # buy pressure lifts center


def _trending_book(n: int = 80, base: float = 100.0, step: float = 0.03, half: float = 0.01):
    rows = []
    ts0 = 1_700_000_000_000
    for i in range(n):
        mid = base + step * i
        ts = ts0 + i * 1000
        rows.append({"snapshot_ts": ts, "side": "bid", "level": 0,
                     "price": round(mid - half, 4), "amount": 10.0})
        rows.append({"snapshot_ts": ts, "side": "ask", "level": 0,
                     "price": round(mid + half, 4), "amount": 10.0})
    return pd.DataFrame(rows)


def test_inventory_skew_holds_less_inventory_than_symmetric():
    """On a trending book the naive quoter accumulates to the limit; the skewed one self-corrects."""
    book = _trending_book(n=80)
    uni = Universe(name="mm", instruments=[_instrument()])
    eng = MarketMakingEngine(cost_model=BpsCostModel(0.0), initial_equity=10_000.0,
                             queue_model=False, levels=1)
    sym = eng.run_quoting(SymmetricMM(SymmetricMMParams(half_spread_bps=1.0, q_max=50.0)), {SYM: book}, uni)
    lin = eng.run_quoting(
        LinearInventoryMM(LinearInventoryMMParams(half_spread_bps=1.0, skew_bps=25.0,
                                                  obi_alpha=0.0, q_max=50.0)), {SYM: book}, uni)
    s_sym, s_lin = mm_summary(sym), mm_summary(lin)
    assert s_sym["n_fills"] > 0 and s_lin["n_fills"] > 0
    assert s_lin["inv_max_abs"] < s_sym["inv_max_abs"]         # skew curbs runaway inventory


# ── end-to-end synthetic backtest ─────────────────────────────────────────────────
def _oscillating_book(n: int = 40, base: float = 100.0, step: float = 0.02, half: float = 0.01):
    pattern = [0.0, -step, 0.0, step]
    rows = []
    ts0 = 1_700_000_000_000
    for i in range(n):
        mid = base + pattern[i % 4]
        ts = ts0 + i * 1000
        rows.append({"snapshot_ts": ts, "side": "bid", "level": 0,
                     "price": round(mid - half, 4), "amount": 10.0})
        rows.append({"snapshot_ts": ts, "side": "ask", "level": 0,
                     "price": round(mid + half, 4), "amount": 10.0})
    return pd.DataFrame(rows)


def test_end_to_end_synthetic_backtest():
    book = _oscillating_book(n=40)
    uni = Universe(name="mm", instruments=[_instrument()])
    # Tight spread so the AS quotes sit at the touch and the 0.02 oscillation crosses them.
    params = ASParams(gamma=0.01, kappa=100.0, horizon=1.0, q_max=5.0, quote_size=1.0,
                      sigma_window=50, join_only=True, inv_soften=True, obi_alpha=0.0)
    engine = MarketMakingEngine(cost_model=BpsCostModel(0.0), initial_equity=10_000.0,
                                queue_model=False, levels=1)
    result = engine.run_quoting(AvellanedaStoikovMM(params), {SYM: book}, uni)

    # (a) accounting identity at every recorded row: equity == cash + position·mid.
    mid = book_features(book, levels=1)["mid"].reindex(result.equity_curve.index)
    lhs = (result.cash + result.positions[SYM] * mid).to_numpy()
    assert np.allclose(lhs, result.equity_curve.to_numpy(), atol=1e-6)

    # (b) it actually quoted and traded.
    assert result.meta["n_quotes"] > 0 and len(result.trades) > 0

    # (c) inventory stays within the hard limit.
    assert result.positions[SYM].abs().max() <= 5.0

    # (d) passive quoting earned spread, and the MM scorecard yields finite metrics.
    summ = mm_summary(result, mid=mid)
    assert summ["spread_captured_bps"] > 0
    assert np.isfinite(summ["sharpe"]) and summ["n_fills"] > 0


# ── predictive OBI alpha overlay ──────────────────────────────────────────────────
def test_forward_return_on_obi_recovers_slope():
    """The markout regression recovers the bps-per-OBI lead from a clean signal."""
    n, lead = 200, 8.0
    z = np.sin(np.linspace(0.0, 20.0, n)) * 0.5                 # varied OBI in [-0.5, 0.5]
    mid = np.empty(n)
    mid[0] = 100.0
    for i in range(1, n):
        mid[i] = mid[i - 1] * (1.0 + lead * z[i - 1] / 1e4)    # fwd return = lead·OBI (bps)
    feat = pd.DataFrame({"mid": mid, "obi": z})
    alpha_bps, r2 = forward_return_on_obi(feat, horizon=1)
    assert alpha_bps == pytest.approx(lead, rel=0.05) and r2 > 0.9


def _informative_book(n=600, base=100.0, spread_bps=4.0, depth=8.0, lead_bps=12.0,
                      ar=0.9, seed=3):
    """A book whose imbalance LEADS the mid: OBI = z (an AR(1) latent), and the next mid step is
    ``lead_bps·z``. A maker that shifts fair value by the OBI forecast should dodge adverse fills."""
    rng = np.random.default_rng(seed)
    z = np.zeros(n)
    for i in range(1, n):
        z[i] = float(np.clip(ar * z[i - 1] + rng.normal(0.0, 0.3), -0.9, 0.9))
    half = base * spread_bps / 2.0 / 1e4
    mid = np.empty(n)
    mid[0] = base
    for i in range(1, n):
        mid[i] = mid[i - 1] + lead_bps * z[i - 1] * base / 1e4 + rng.normal(0.0, base * 1e-4)
    rows = []
    ts0 = 1_700_000_000_000
    for i in range(n):
        m = mid[i]
        rows.append({"snapshot_ts": ts0 + i * 1000, "side": "bid", "level": 0,
                     "price": round(m - half, 4), "amount": depth * (1.0 + z[i])})
        rows.append({"snapshot_ts": ts0 + i * 1000, "side": "ask", "level": 0,
                     "price": round(m + half, 4), "amount": depth * (1.0 - z[i])})
    return pd.DataFrame(rows)


def test_alpha_quoter_improves_pnl_via_adverse_selection_defense():
    """On an OBI-informative book the calibrated alpha overlay improves realized PnL: it leans
    away from adverse fills (so it loses less to adverse selection) while still trading."""
    book = _informative_book(n=800, lead_bps=15.0)
    uni = Universe(name="mm", instruments=[_instrument()])
    feat = book_features(book, levels=1)
    alpha_bps, _ = forward_return_on_obi(feat, horizon=1)
    assert alpha_bps > 0                                        # imbalance leads price up

    eng = MarketMakingEngine(cost_model=BpsCostModel(0.0), initial_equity=1_000_000.0,
                             queue_model=False, levels=1)
    base = eng.run_quoting(LinearInventoryMM(LinearInventoryMMParams(
        half_spread_bps=4.0, skew_bps=8.0, obi_alpha=0.5, q_max=50.0)), {SYM: book}, uni)
    alpha = eng.run_quoting(AlphaQuoterMM(AlphaQuoterMMParams(
        half_spread_bps=4.0, skew_bps=8.0, alpha_bps=alpha_bps, alpha_gain=0.25, q_max=50.0)),
        {SYM: book}, uni)
    s_base, s_alpha = mm_summary(base, mid=feat["mid"]), mm_summary(alpha, mid=feat["mid"])
    assert s_alpha["n_fills"] > 0                               # still makes markets (not withdrawn)
    assert s_alpha["total_return"] > s_base["total_return"]     # better realized PnL


# ── taker mode (capture the move, not just withdraw) ──────────────────────────────
def test_matcher_taker_fill_on_cross():
    m = LimitOrderMatchingHandler(cost_model=BpsCostModel(0.0),
                                  taker_cost_model=BpsCostModel(10.0), queue_model=False)
    instr = _instrument()
    m.on_book(_book(0, 99.0, 10, 101.0, 10), instr)            # prev_book: mid 100, best_ask 101
    fills = m.post(QuoteEvent(timestamp=pd.Timestamp(0, unit="ms", tz="UTC"), instrument=SYM,
                              bid_px=101.0, bid_size=2.0), instr)    # marketable buy (crosses)
    assert len(fills) == 1
    f = fills[0]
    assert f.delta_units == pytest.approx(2.0) and f.fill_price == pytest.approx(101.0)  # at the ask
    assert f.ref_price == pytest.approx(100.0)                 # mid
    assert f.commission == pytest.approx(2.0 * 101.0 * 10.0 / 1e4)        # taker fee
    assert f.slippage == pytest.approx(2.0 * abs(101.0 - 100.0))          # spread crossed
    # a non-crossing bid just rests (no immediate fill)
    assert m.post(QuoteEvent(timestamp=pd.Timestamp(1, unit="ms", tz="UTC"), instrument=SYM,
                             bid_px=99.0, bid_size=1.0), instr) == []


def test_alpha_taker_captures_move():
    """With a strong calibrated signal, crossing to take beats passively withdrawing."""
    book = _informative_book(n=800, lead_bps=15.0)
    uni = Universe(name="mm", instruments=[_instrument()])
    feat = book_features(book, levels=1)
    alpha_bps, _ = forward_return_on_obi(feat, horizon=1)
    eng = MarketMakingEngine(cost_model=BpsCostModel(0.0), taker_cost_model=BpsCostModel(0.0),
                             initial_equity=1_000_000.0, queue_model=False, levels=1)
    passive = eng.run_quoting(AlphaQuoterMM(AlphaQuoterMMParams(
        half_spread_bps=4.0, skew_bps=8.0, alpha_bps=alpha_bps, alpha_gain=0.3, q_max=50.0)),
        {SYM: book}, uni)
    taker = eng.run_quoting(AlphaQuoterMM(AlphaQuoterMMParams(
        half_spread_bps=4.0, skew_bps=8.0, alpha_bps=alpha_bps, alpha_gain=1.0, q_max=50.0,
        take_threshold_bps=1.0, taker_fee_bps=0.0)), {SYM: book}, uni)
    s_p, s_t = mm_summary(passive, mid=feat["mid"]), mm_summary(taker, mid=feat["mid"])
    assert s_t["total_return"] > s_p["total_return"]           # taking captures the predicted move


# ── paper quoting loop (the live/paper stage) ─────────────────────────────────────
def test_paper_quoting_loop_runs_and_accounts():
    book = _oscillating_book(n=40)
    loop = PaperQuotingLoop(
        LinearInventoryMM(LinearInventoryMMParams(half_spread_bps=1.0, skew_bps=5.0, q_max=20.0)),
        _instrument(), cost_model=BpsCostModel(0.0), initial_equity=10_000.0)
    states = loop.run(BookReplayDataHandler({SYM: book}, levels=1).stream())
    assert states and states[-1].n_fills > 0 and not states[-1].halted
    res = loop.result()
    mid = book_features(book, levels=1)["mid"].reindex(res.equity_curve.index)
    lhs = (res.cash + res.positions[SYM] * mid).to_numpy()
    assert np.allclose(lhs, res.equity_curve.to_numpy(), atol=1e-6)     # accounting identity


def test_paper_quoting_kill_switch_halts():
    rows = []                                                  # a steadily crashing book
    ts0 = 1_700_000_000_000
    for i in range(200):
        m = 100.0 - 0.05 * i
        rows.append({"snapshot_ts": ts0 + i * 1000, "side": "bid", "level": 0,
                     "price": round(m - 0.01, 4), "amount": 10.0})
        rows.append({"snapshot_ts": ts0 + i * 1000, "side": "ask", "level": 0,
                     "price": round(m + 0.01, 4), "amount": 10.0})
    book = pd.DataFrame(rows)
    loop = PaperQuotingLoop(
        SymmetricMM(SymmetricMMParams(half_spread_bps=1.0, q_max=1e9)), _instrument(),
        cost_model=BpsCostModel(0.0), initial_equity=10_000.0,
        risk=QuotingRiskLimits(max_drawdown_kill=0.02))
    states = loop.run(BookReplayDataHandler({SYM: book}, levels=1).stream())
    assert any(s.halted for s in states)                       # kill-switch tripped
    halt_idx = next(i for i, s in enumerate(states) if s.halted)
    assert states[-1].halted
    assert states[-1].n_fills == states[halt_idx].n_fills      # no fills after the halt
