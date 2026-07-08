"""Build + evaluate the high-frequency market-making strategies on synthetic order books.

Runs the three quoting strategies — the naive **SymmetricMM** baseline, the scale-free
**LinearInventoryMM** (bps spread + inventory skew + OBI tilt), and the textbook
**AvellanedaStoikovMM** — through the ``MarketMakingEngine`` over two synthetic L2 regimes
(mean-reverting and trending), and prints the market-making scorecard (spread captured, net edge,
fill ratio, inventory, markout / adverse selection). Then sweeps the inventory-skew knob to show
inventory management at work. Fully offline + seeded — no recorded depth needed (record real books
with scripts/pull_orderbook_stream.py for true-depth runs).

  .venv\\Scripts\\python.exe scripts\\build_market_maker.py

Note: the bps-parametrized quoters work out of the box; AvellanedaStoikovMM's spread is in
*absolute* price units, so its γ/κ are tuned here to the synthetic asset (see calibrate_as_params.py).
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.eventdriven.data_book import BookReplayDataHandler
from qhfi.backtest.eventdriven.engine import MarketMakingEngine
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.data.microstructure import book_features, forward_return_on_obi
from qhfi.evaluation.mm_metrics import mm_summary
from qhfi.strategy.library.mm.alpha_quoter import AlphaQuoterMM, AlphaQuoterMMParams
from qhfi.strategy.library.mm.avellaneda_stoikov import ASParams, AvellanedaStoikovMM
from qhfi.strategy.library.mm.linear_inventory import LinearInventoryMM, LinearInventoryMMParams
from qhfi.strategy.library.mm.symmetric import SymmetricMM, SymmetricMMParams
from qhfi.trading.quoting_loop import PaperQuotingLoop, QuotingRiskLimits

SYM = "BTC/USDT"
EQUITY = 1_000_000.0


def synthetic_book(kind: str, n: int = 3000, base: float = 100.0, spread_bps: float = 2.0,
                   depth: float = 8.0, seed: int = 0) -> pd.DataFrame:
    """A long-format L2 book whose mid follows a mean-reverting or trending path (real-ish
    micro-dynamics, modelled depth). Per-level size is tilted by the last move so OBI varies."""
    rng = np.random.default_rng(seed)
    mid = np.empty(n)
    mid[0] = base
    half = base * spread_bps / 2.0 / 1e4
    sig = base * 3e-4                                          # ~3 bps per-step noise
    for i in range(1, n):
        rev = 0.3 * (base - mid[i - 1]) if kind == "meanrev" else 0.0
        drift = 0.0 if kind == "meanrev" else base * 1.5e-5   # gentle up-trend
        mid[i] = mid[i - 1] + rev + drift + rng.normal(0.0, sig)

    ts0, levels, rows = 1_700_000_000_000, 3, []
    for i in range(n):
        m = mid[i]
        d = mid[i] - mid[i - 1] if i else 0.0
        tilt = float(np.clip(d / half * 0.3, -0.8, 0.8)) if half else 0.0
        for lv in range(levels):
            step = half * (1 + 2 * lv)
            rows.append((ts0 + i * 1000, "bid", lv, round(m - step, 4), depth * (1 + tilt) * (1 - 0.1 * lv)))
            rows.append((ts0 + i * 1000, "ask", lv, round(m + step, 4), depth * (1 - tilt) * (1 - 0.1 * lv)))
    return pd.DataFrame(rows, columns=["snapshot_ts", "side", "level", "price", "amount"])


def informative_book(n: int = 3000, base: float = 100.0, spread_bps: float = 4.0,
                     depth: float = 8.0, lead_bps: float = 12.0, ar: float = 0.9,
                     seed: int = 3) -> pd.DataFrame:
    """A book whose imbalance LEADS the mid: OBI = z (an AR(1) latent) and the next mid step is
    ``lead_bps·z``. The regime where a predictive OBI overlay can actually beat adverse selection."""
    rng = np.random.default_rng(seed)
    z = np.zeros(n)
    for i in range(1, n):
        z[i] = float(np.clip(ar * z[i - 1] + rng.normal(0.0, 0.3), -0.9, 0.9))
    half = base * spread_bps / 2.0 / 1e4
    mid = np.empty(n)
    mid[0] = base
    for i in range(1, n):
        mid[i] = mid[i - 1] + lead_bps * z[i - 1] * base / 1e4 + rng.normal(0.0, base * 1e-4)
    ts0, rows = 1_700_000_000_000, []
    for i in range(n):
        m = mid[i]
        rows.append((ts0 + i * 1000, "bid", 0, round(m - half, 4), depth * (1.0 + z[i])))
        rows.append((ts0 + i * 1000, "ask", 0, round(m + half, 4), depth * (1.0 - z[i])))
    return pd.DataFrame(rows, columns=["snapshot_ts", "side", "level", "price", "amount"])


def evaluate(strat, book: pd.DataFrame, uni: Universe, taker_bps: float = 10.0) -> dict:
    eng = MarketMakingEngine(cost_model=BpsCostModel(1.0), taker_cost_model=BpsCostModel(taker_bps),
                             initial_equity=EQUITY, queue_model=False, levels=3)
    result = eng.run_quoting(strat, {SYM: book}, uni)
    mid = book_features(book, levels=3)["mid"]
    s = mm_summary(result, mid=mid)
    return {
        "pnl_%": round(s["total_return"] * 100, 3),
        "spread_cap_bps": round(s["spread_captured_bps"], 2),
        "adv_sel_bps": round(s.get("markout_1_bps", float("nan")), 2),   # markout @ +1 (negative = adverse)
        "net_edge_bps": round(s["net_edge_bps"], 2),
        "fills": int(s["n_fills"]),
        "fill_ratio": round(s["fill_ratio"], 3),
        "inv_max": round(s["inv_max_abs"], 1),
        "inv_half_life": None if not np.isfinite(s["inv_half_life"]) else round(s["inv_half_life"], 0),
    }


def strategies() -> dict:
    return {
        "SymmetricMM (baseline)": SymmetricMM(SymmetricMMParams(half_spread_bps=2.0, q_max=50.0)),
        "LinearInventoryMM": LinearInventoryMM(LinearInventoryMMParams(
            half_spread_bps=2.0, skew_bps=12.0, obi_alpha=0.5, q_max=50.0)),
        "AvellanedaStoikovMM": AvellanedaStoikovMM(ASParams(
            gamma=0.5, kappa=200.0, obi_alpha=0.5, q_max=50.0, sigma_window=100)),
    }


def main() -> None:
    uni = Universe(name="mm", instruments=[
        Instrument(id=SYM, asset_class=AssetClass.CRYPTO, exchange="synthetic", lot_size=1e-12)])

    for kind, label in [("meanrev", "MEAN-REVERTING"), ("trend", "TRENDING")]:
        book = synthetic_book(kind, seed=7)
        rows = {name: evaluate(strat, book, uni) for name, strat in strategies().items()}
        print(f"\n=== {label} book ({len(book['snapshot_ts'].unique())} snapshots) ===")
        print(pd.DataFrame(rows).T.to_string())

    # Inventory-skew sweep on the trending book — the knob that manages inventory.
    print("\n=== LinearInventoryMM · inventory-skew sweep (TRENDING book) ===")
    book = synthetic_book("trend", seed=7)
    sweep = {}
    for skew in [0.0, 4.0, 8.0, 16.0, 32.0]:
        strat = LinearInventoryMM(LinearInventoryMMParams(
            half_spread_bps=2.0, skew_bps=skew, obi_alpha=0.0, q_max=50.0))
        sweep[f"skew={skew:g}bps"] = evaluate(strat, book, uni)
    print(pd.DataFrame(sweep).T[["inv_max", "inv_half_life", "net_edge_bps", "fill_ratio"]].to_string())
    print(
        "\nReading it: the market-maker CAPTURES the spread (positive spread_cap_bps) but pays "
        "ADVERSE SELECTION (negative adv_sel_bps — the cross fill model fills a passive quote on "
        "the very move that goes against it), so net edge is thin/negative on these signal-free "
        "synthetic books — the honest baseline. The clear win is inventory control: raising skew "
        "drives inv_max 40→4 (Symmetric runs to the q_max limit on the trend; the skewed quoter "
        "self-corrects), trading fills for inventory risk."
    )

    # ── predictive OBI alpha overlay: calibrate, then quote ahead of the move ──
    print("\n=== PREDICTIVE OBI OVERLAY (book where imbalance leads price) ===")
    book = informative_book(seed=3)
    feat = book_features(book, levels=1)
    alpha_bps, r2 = forward_return_on_obi(feat, horizon=1)
    print(f"calibrated OBI alpha: {alpha_bps:.2f} bps per unit OBI (R²={r2:.3f})")
    overlay = {
        "SymmetricMM": SymmetricMM(SymmetricMMParams(half_spread_bps=4.0, q_max=50.0)),
        "LinearInventoryMM (OBI tilt)": LinearInventoryMM(LinearInventoryMMParams(
            half_spread_bps=4.0, skew_bps=8.0, obi_alpha=0.5, q_max=50.0)),
        "AlphaQuoterMM (passive)": AlphaQuoterMM(AlphaQuoterMMParams(
            half_spread_bps=4.0, skew_bps=8.0, alpha_bps=alpha_bps, alpha_gain=0.3, q_max=50.0)),
    }
    rows = {n: evaluate(s, book, uni) for n, s in overlay.items()}
    # Taker mode: on a 2 bps-taker venue, cross the spread when the predicted edge beats the cost.
    rows["AlphaQuoterMM (taker)"] = evaluate(AlphaQuoterMM(AlphaQuoterMMParams(
        half_spread_bps=4.0, skew_bps=8.0, alpha_bps=alpha_bps, alpha_gain=1.0, q_max=50.0,
        take_threshold_bps=1.0, taker_fee_bps=2.0)), book, uni, taker_bps=2.0)
    print(pd.DataFrame(rows).T.to_string())
    print("\nThe passive overlay leans AWAY from adverse fills → better pnl_% and less negative "
          "adv_sel_bps than the OBI-tilt-only quoter (it withdraws when it foresees the move). The "
          "TAKER variant goes further: when the predicted edge beats the crossing cost it crosses "
          "the spread to CAPTURE the move (positive adv_sel_bps — it's now on the right side of the "
          "trade), turning the signal into realized PnL. The overlay is only as good as the signal: "
          "alpha_bps≈0 → it reduces to LinearInventoryMM.")

    paper_demo(uni)


def paper_demo(uni: Universe) -> None:
    """The live/paper stage: drive a quoter through PaperQuotingLoop with risk gates."""
    instr = uni.instruments[0]
    print("\n=== PAPER QUOTING LOOP (live-style replay + risk gates) ===")
    book = synthetic_book("meanrev", n=1500, seed=11)
    loop = PaperQuotingLoop(
        LinearInventoryMM(LinearInventoryMMParams(half_spread_bps=3.0, skew_bps=10.0, q_max=30.0)),
        instr, cost_model=BpsCostModel(1.0), initial_equity=1_000_000.0,
        risk=QuotingRiskLimits(max_inventory=30.0, max_drawdown_kill=0.05))
    st = loop.run(BookReplayDataHandler({instr.id: book}, levels=3).stream())[-1]
    print(f"  ran {len(book['snapshot_ts'].unique())} book updates → "
          f"inv={st.inventory:+.1f}  equity=${st.equity:,.0f}  pnl=${st.pnl:+,.0f}  "
          f"fills={st.n_fills}  halted={st.halted}")

    # A crash → the maker buys the falling knife → the drawdown kill-switch cancels quotes + stops.
    rows, ts0 = [], 1_700_000_000_000
    for i in range(200):
        m = 100.0 - 0.05 * i
        rows.append((ts0 + i * 1000, "bid", 0, round(m - 0.01, 4), 10.0))
        rows.append((ts0 + i * 1000, "ask", 0, round(m + 0.01, 4), 10.0))
    crash = pd.DataFrame(rows, columns=["snapshot_ts", "side", "level", "price", "amount"])
    loop = PaperQuotingLoop(
        SymmetricMM(SymmetricMMParams(half_spread_bps=1.0, q_max=1e9)), instr,
        cost_model=BpsCostModel(0.0), initial_equity=10_000.0,
        risk=QuotingRiskLimits(max_drawdown_kill=0.02))
    states = loop.run(BookReplayDataHandler({instr.id: crash}, levels=1).stream())
    final = states[-1]
    halt = next((i for i, s in enumerate(states) if s.halted), -1)
    if halt < 0:
        print("  crash book: kill-switch did not trip (raise the crash speed or lower the limit).")
    else:
        print(f"  crash book: kill-switch tripped at update {halt} ({final.reason}); "
              f"halted with inv={states[halt].inventory:+.0f}, "
              f"{final.n_fills - states[halt].n_fills} fills after the halt.")


if __name__ == "__main__":
    main()
