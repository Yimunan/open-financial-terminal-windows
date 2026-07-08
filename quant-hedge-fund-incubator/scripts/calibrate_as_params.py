"""Calibrate Avellaneda–Stoikov inputs (σ, κ, A) per symbol from recorded data.

Read-only over the lake. Estimates:
  * **σ** — rolling realized vol of the mid (per-snapshot units), median over the sample.
  * **κ, A** — the order-arrival law ``λ(δ) = A·e^{−κδ}``, fit by regressing ``ln(count)`` of
    fills on their distance ``δ`` from the contemporaneous mid. Uses the trade tape when present
    (``TradeStore``), else approximates fills from top-of-book crossings between snapshots.

  .venv\\Scripts\\python.exe scripts\\calibrate_as_params.py --symbol BTC/USDT

Caveat: κ from snapshot crossings (no tape) is biased — snapshot transitions conflate cancels,
fills, and replenishment. Treat the printed κ as a starting point and sweep a range in backtest.
"""

from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from qhfi.data.highfreq import OrderBookStore, TradeStore
from qhfi.data.lake import lake_root
from qhfi.data.microstructure import book_features, fit_arrival_intensity, realized_vol


def _kappa_from_trades(trades: pd.DataFrame, feat: pd.DataFrame, n_bins: int = 12) -> tuple[float, float]:
    mid = feat["mid"].sort_index()
    ts = pd.to_datetime(trades["ts"], unit="ms", utc=True)
    aligned = mid.reindex(mid.index.union(ts)).ffill().reindex(ts)
    dist = (trades["price"].to_numpy() - aligned.to_numpy())
    dist = np.abs(dist[np.isfinite(dist)])
    if len(dist) < n_bins:
        return 0.0, 1.0
    edges = np.linspace(0, np.quantile(dist, 0.95), n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    counts, _ = np.histogram(dist, bins=edges)
    return fit_arrival_intensity(centers, counts)


def _kappa_from_book_crossings(feat: pd.DataFrame, n_bins: int = 12) -> tuple[float, float]:
    # Proxy: when the mid jumps between snapshots, treat the move size as a "fill distance".
    moves = np.abs(feat["mid"].diff().dropna().to_numpy())
    moves = moves[moves > 0]
    if len(moves) < n_bins:
        return 0.0, 1.0
    edges = np.linspace(0, np.quantile(moves, 0.95), n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    counts, _ = np.histogram(moves, bins=edges)
    return fit_arrival_intensity(centers, counts)


def main() -> None:
    ap = argparse.ArgumentParser(description="calibrate AS params from the lake")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--source", default="okx")
    ap.add_argument("--levels", type=int, default=10)
    ap.add_argument("--sigma-window", type=int, default=100)
    args = ap.parse_args()

    root = lake_root()
    obs = OrderBookStore(root)
    if not obs.has(args.symbol, source=args.source):
        raise SystemExit(f"no recorded book for {args.symbol} ({args.source}) — "
                         f"run scripts/pull_orderbook_stream.py first.")
    feat = book_features(obs.load(args.symbol, source=args.source), levels=args.levels)

    sigma = float(np.nanmedian(realized_vol(feat["mid"], args.sigma_window)))

    tstore = TradeStore(root)
    if tstore.has(args.symbol, source=args.source):
        trades = tstore.load(args.symbol, source=args.source)
        a, kappa = _kappa_from_trades(trades, feat)
        src = f"trade tape ({len(trades)} prints)"
    else:
        a, kappa = _kappa_from_book_crossings(feat)
        src = "book crossings (no tape — biased; sweep κ in backtest)"

    print(f"\nAvellaneda–Stoikov calibration — {args.symbol} @ {args.source}")
    print(f"  snapshots      : {len(feat)}")
    print(f"  sigma (per-snap): {sigma:.3e}")
    print(f"  kappa          : {kappa:.4f}")
    print(f"  A (intensity)  : {a:.4f}")
    print(f"  source         : {src}\n")
    print("Suggested ASParams overrides:")
    print(f"  sigma_window={args.sigma_window}  kappa={kappa:.3f}  (gamma/horizon/q_max: tune in backtest)")


if __name__ == "__main__":
    main()
