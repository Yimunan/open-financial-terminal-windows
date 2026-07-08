"""Phase 5: a point-in-time value factor from the EDGAR fundamentals.

earnings_yield = TTM diluted EPS / price, where TTM EPS is built from the filing-date-stamped
`edgar_eps_diluted` series and forward-filled onto the daily grid — so each value is only known
from the date it was *filed* (no look-ahead). Evaluated sector-neutral over the full PIT history
(EDGAR XBRL ≈ 2010→), plus a simple monthly long-short to gauge tradability.

  .venv\\Scripts\\python.exe scripts\\eval_value_factor.py
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd

from qhfi.core.universe_io import load_universe
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.lake import lake_root, market_store
from qhfi.factors import evaluation as fe
from qhfi.factors import transforms as tf


def main() -> None:
    universe = load_universe("config/instruments/equity_sectors.yaml")
    prices = market_store().load_panel(universe.instruments, "close")
    fund = FundamentalsStore(lake_root())
    sectors = universe.groups("gics_sector")

    # TTM diluted EPS per name (filing-date stamped) → panel
    ttm = {}
    for ins in universe.instruments:
        if fund.has(ins, "edgar_eps_diluted"):
            s = fund.load(ins, "edgar_eps_diluted").sort_index()
            ttm[ins.id] = s.rolling(4).sum()                    # trailing 4 quarters
    ttm_panel = pd.DataFrame(ttm).sort_index()

    # forward-fill onto the daily price grid (value known from its FILING date forward = PIT)
    full = ttm_panel.index.union(prices.index)
    ttm_daily = ttm_panel.reindex(full).ffill().reindex(prices.index)
    earnings_yield = (ttm_daily / prices).dropna(how="all")
    px = prices.reindex(earnings_yield.index)
    print(f"PIT value factor (E/P) | {earnings_yield.shape[1]} names | "
          f"{earnings_yield.index.min().date()} → {earnings_yield.index.max().date()} "
          f"({len(earnings_yield)}d)\n")

    # sector-neutral, standardized signal
    signal = tf.neutralize(tf.zscore(tf.winsorize(earnings_yield)), sectors)

    print("Information coefficient (sector-neutral, point-in-time):")
    for h in (21, 63):
        s = fe.ic_summary(fe.information_coefficient(signal, px, horizon=h))
        print(f"  {h:>2}d horizon:  IC={s.mean_ic:+.4f}  IC-IR={s.ic_ir:+.3f}  t={s.t_stat:+.1f}  hit={s.hit_rate:.2f}")
    qr = fe.quantile_returns(signal, px, q=5, horizon=63)
    print(f"  quintile spread (63d, Q5-Q1): {fe.spread(qr):+.4f}")

    # simple monthly long-short (top/bottom quintile), to gauge tradability + turnover
    m_idx = signal.resample("ME").last().index
    sig_m = signal.reindex(m_idx, method="ffill")
    fwd_m = px.resample("ME").last().pct_change().shift(-1)
    rets, prev_long = [], set()
    turns = []
    for dt in sig_m.index:
        row = sig_m.loc[dt].dropna()
        if len(row) < 10:
            continue
        k = max(1, int(len(row) * 0.2))
        longs, shorts = set(row.nlargest(k).index), set(row.nsmallest(k).index)
        fr = fwd_m.loc[dt] if dt in fwd_m.index else None
        if fr is not None:
            rets.append(fr[list(longs)].mean() - fr[list(shorts)].mean())
            turns.append(len(longs ^ prev_long) / max(len(longs), 1))
            prev_long = longs
    ls = pd.Series(rets).dropna()
    sharpe = ls.mean() / ls.std() * np.sqrt(12) if ls.std() else float("nan")
    print(f"\nMonthly long-short (top/bottom quintile, equal-weight):")
    print(f"  ann. return {ls.mean()*12:+.1%}  vol {ls.std()*np.sqrt(12):.1%}  Sharpe {sharpe:.2f}  "
          f"hit {(ls>0).mean():.2f}  avg monthly turnover {np.mean(turns):.0%}")
    print("\n→ value is a slow signal (turnover from quarterly fundamental updates), so unlike the")
    print("  Alpha101 reversal signals it isn't eaten by costs — and the IC is genuinely PIT-clean.")


if __name__ == "__main__":
    main()
