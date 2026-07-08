"""Performance metrics computed from a daily net-return series.

Reference implementations are simple enough to live here directly (quantstats is used only
for the rich HTML tearsheet). All annualization assumes 252 trading days; pass a different
``periods_per_year`` for 24/7 crypto (≈365) if you want calendar-consistent figures.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def cagr(returns: pd.Series, periods_per_year: int = 252) -> float:
    n = len(returns.dropna())
    if n == 0:
        return 0.0
    total = (1 + returns.fillna(0)).prod()
    return float(total ** (periods_per_year / n) - 1)


def ann_vol(returns: pd.Series, periods_per_year: int = 252) -> float:
    return float(returns.std(ddof=0) * np.sqrt(periods_per_year))


def sharpe(returns: pd.Series, rf: float = 0.0, periods_per_year: int = 252) -> float:
    excess = returns.fillna(0) - rf / periods_per_year
    sd = excess.std(ddof=0)
    if sd == 0:
        return 0.0
    return float(excess.mean() / sd * np.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: int = 252) -> float:
    r = returns.fillna(0)
    downside = r[r < 0].std(ddof=0)
    if downside == 0:
        return 0.0
    return float(r.mean() / downside * np.sqrt(periods_per_year))


def max_drawdown(returns: pd.Series) -> float:
    curve = (1 + returns.fillna(0)).cumprod()
    peak = curve.cummax()
    return float((curve / peak - 1).min())


def calmar(returns: pd.Series, periods_per_year: int = 252) -> float:
    mdd = abs(max_drawdown(returns))
    return float(cagr(returns, periods_per_year) / mdd) if mdd else 0.0


def summary(returns: pd.Series, periods_per_year: int = 252) -> dict[str, float]:
    return {
        "cagr": cagr(returns, periods_per_year),
        "ann_vol": ann_vol(returns, periods_per_year),
        "sharpe": sharpe(returns, periods_per_year=periods_per_year),
        "sortino": sortino(returns, periods_per_year),
        "max_drawdown": max_drawdown(returns),
        "calmar": calmar(returns, periods_per_year),
    }


# ── trade-level analytics (from a per-close realized-P&L list) ────────────────
def trade_stats(realized: Sequence[float]) -> dict[str, float | int]:
    """Summary statistics of a sequence of closed-trade realized P&L figures.

    Each element is the realized P&L booked by one position-reducing fill (gross of
    commission, as the SimBroker books it). A zero-P&L close counts as neither a win
    nor a loss. All-zero/empty inputs return zeroed stats rather than raising.
    """
    pnl = [float(x) for x in realized]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    n = len(pnl)
    n_w, n_l = len(wins), len(losses)
    gross_profit = float(sum(wins))
    gross_loss = float(-sum(losses))  # reported positive
    decided = n_w + n_l  # trades with a non-zero outcome
    win_rate = n_w / decided if decided else 0.0
    avg_win = gross_profit / n_w if n_w else 0.0
    avg_loss = -gross_loss / n_l if n_l else 0.0  # negative
    # expectancy per decided trade; profit_factor inf-guarded (no losses → use gross_profit)
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss) if decided else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0)
    payoff_ratio = avg_win / abs(avg_loss) if avg_loss else (avg_win if avg_win else 0.0)
    return {
        "n_trades": n,
        "n_wins": n_w,
        "n_losses": n_l,
        "win_rate": float(win_rate),
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": float(profit_factor),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "payoff_ratio": float(payoff_ratio),
        "expectancy": round(float(expectancy), 6),
        "largest_win": round(max(pnl), 6) if pnl else 0.0,
        "largest_loss": round(min(pnl), 6) if pnl else 0.0,
        "total_realized": round(float(sum(pnl)), 6),
    }


# ── tail risk (historical / empirical) ───────────────────────────────────────
def value_at_risk(returns: pd.Series, level: float = 0.05) -> float:
    """Historical VaR at ``level`` (e.g. 0.05 → 95% VaR), reported as a positive loss
    fraction. The empirical ``level`` quantile of returns; 0.0 on an empty series."""
    r = returns.dropna()
    if r.empty:
        return 0.0
    return float(-np.quantile(r.to_numpy(), level))


def cvar(returns: pd.Series, level: float = 0.05) -> float:
    """Historical conditional VaR (expected shortfall): mean of losses at/under the VaR
    threshold, reported as a positive loss fraction."""
    r = returns.dropna()
    if r.empty:
        return 0.0
    thresh = np.quantile(r.to_numpy(), level)
    tail = r[r <= thresh]
    return float(-tail.mean()) if len(tail) else float(-thresh)


def rolling_sharpe(returns: pd.Series, window: int, periods_per_year: int = 252) -> pd.Series:
    """Rolling annualized Sharpe over ``window`` periods (population std). Windows with
    zero variance yield 0.0 rather than inf/NaN."""
    r = returns.fillna(0)
    mean = r.rolling(window).mean()
    std = r.rolling(window).std(ddof=0)
    rs = (mean / std.replace(0.0, np.nan)) * np.sqrt(periods_per_year)
    return rs.fillna(0.0)


# ── benchmark-relative ───────────────────────────────────────────────────────
def benchmark_stats(
    returns: pd.Series, benchmark: pd.Series, periods_per_year: int = 252
) -> dict[str, float]:
    """Performance of ``returns`` relative to a ``benchmark`` return series.

    Both are aligned on their common (inner-join) index first, so callers can pass
    differently-indexed series. Needs ≥2 overlapping points; returns zeroed stats below
    that. ``alpha`` is annualized (CAPM intercept × periods_per_year), ``tracking_error``
    and ``information_ratio`` annualize the active-return series, and up/down capture are
    the ratio of mean returns on the benchmark's up/down periods.
    """
    df = pd.concat([returns.rename("p"), benchmark.rename("b")], axis=1, join="inner").dropna()
    keys = (
        "beta", "alpha", "tracking_error", "information_ratio",
        "up_capture", "down_capture", "correlation",
    )
    if len(df) < 2:
        return {k: 0.0 for k in keys}
    p, b = df["p"].to_numpy(), df["b"].to_numpy()
    var_b = float(np.var(b))
    beta = float(np.cov(p, b, ddof=0)[0, 1] / var_b) if var_b else 0.0
    alpha = float((p.mean() - beta * b.mean()) * periods_per_year)
    active = p - b
    te = float(active.std(ddof=0) * np.sqrt(periods_per_year))
    ir = float(active.mean() / active.std(ddof=0) * np.sqrt(periods_per_year)) if active.std(ddof=0) else 0.0
    up = b > 0
    down = b < 0
    up_cap = float(p[up].mean() / b[up].mean()) if up.any() and b[up].mean() else 0.0
    down_cap = float(p[down].mean() / b[down].mean()) if down.any() and b[down].mean() else 0.0
    corr = float(np.corrcoef(p, b)[0, 1]) if var_b and float(np.var(p)) else 0.0
    return {
        "beta": beta,
        "alpha": alpha,
        "tracking_error": te,
        "information_ratio": ir,
        "up_capture": up_cap,
        "down_capture": down_cap,
        "correlation": corr,
    }
