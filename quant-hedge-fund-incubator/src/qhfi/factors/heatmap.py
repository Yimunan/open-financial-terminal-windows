"""Cross-factor heatmaps — the visual companion to ``factors.evaluation``.

``eval_alpha101`` ranks factors in a flat table; this module instead lays factors out
against each other so collinearity, IC stability over time, and decay are visible at a
glance — the diagnostics a researcher needs *before* blending signals (the VIF-prune /
IC-weight step in ``factors.selection``).

Four labeled matrices, each a plain ``pd.DataFrame`` (compute is kept separate from render
so the matrices are testable), plus one Rich-colored console renderer:

* ``factor_correlation`` — factor × factor pooled cross-correlation (who is redundant).
* ``ic_over_time``       — period × factor mean IC (is the edge stable or regime-dependent).
* ``ic_scorecard``       — factor × metric (mean_ic / ic_ir / t_stat / hit_rate / Q-spread).
* ``ic_decay_matrix``    — factor × horizon mean IC (how fast the edge fades).

All builders reuse ``factors.evaluation`` — none reimplement IC.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from rich.console import Console
from rich.style import Style
from rich.table import Table
from rich.text import Text

from qhfi.core.types import Panel
from qhfi.factors.evaluation import (
    ic_decay,
    ic_summary,
    information_coefficient,
    quantile_returns,
    spread,
)

SCORECARD_METRICS = ["mean_ic", "ic_ir", "t_stat", "hit_rate", "Q_spread"]


# --------------------------------------------------------------------------- builders

def factor_correlation(signals: dict[str, Panel], method: str = "spearman") -> pd.DataFrame:
    """Factor × factor cross-correlation over the pooled (date × instrument) observations.

    Uses the same stacking as ``selection.vif_prune`` so the view matches what VIF sees.
    """
    pooled = pd.DataFrame({name: panel.stack() for name, panel in signals.items()}).dropna()
    return pooled.corr(method=method)


def ic_over_time(
    signals: dict[str, Panel], prices: Panel, horizon: int = 5, freq: str = "ME"
) -> pd.DataFrame:
    """Period (rows, resampled at ``freq``) × factor (columns) mean information coefficient."""
    cols = {
        name: information_coefficient(sig, prices, horizon=horizon).resample(freq).mean()
        for name, sig in signals.items()
    }
    return pd.DataFrame(cols)


def ic_scorecard(
    signals: dict[str, Panel], prices: Panel, horizon: int = 5, q: int = 5
) -> pd.DataFrame:
    """Factor (rows) × metric (columns) summary: mean_ic, ic_ir, t_stat, hit_rate, Q-spread."""
    rows: dict[str, dict[str, float]] = {}
    for name, sig in signals.items():
        s = ic_summary(information_coefficient(sig, prices, horizon=horizon))
        qspread = spread(quantile_returns(sig, prices, q=q, horizon=horizon))
        rows[name] = {
            "mean_ic": s.mean_ic,
            "ic_ir": s.ic_ir,
            "t_stat": s.t_stat,
            "hit_rate": s.hit_rate,
            "Q_spread": qspread,
        }
    return pd.DataFrame(rows).T[SCORECARD_METRICS]


def ic_decay_matrix(
    signals: dict[str, Panel],
    prices: Panel,
    horizons: tuple[int, ...] = (1, 2, 3, 5, 10, 21),
) -> pd.DataFrame:
    """Factor (rows) × horizon (columns) mean IC — the per-factor decay profile."""
    cols = {name: ic_decay(sig, prices, horizons=horizons) for name, sig in signals.items()}
    return pd.DataFrame(cols).T


def asset_correlation(
    prices: Panel, groups: dict[str, str] | None = None, method: str = "pearson"
) -> pd.DataFrame:
    """Correlation of daily returns across assets — the cross-asset structure of a universe.

    With ``groups`` (instrument_id → label, e.g. ``universe.groups('gics_sector')`` or an
    asset-class map ``{i.id: i.asset_class.value …}``), members are collapsed into an
    equal-weight basket per group and the group return series are correlated — the
    cross-asset / cross-sector view. Without it, the per-instrument return panel is
    correlated directly.
    """
    rets = prices.pct_change()
    if groups is not None:
        labels = pd.Series({c: groups.get(c, "__none__") for c in rets.columns})
        rets = rets.T.groupby(labels).mean().T  # equal-weight basket return per group
    return rets.corr(method=method)


# --------------------------------------------------------------------------- renderer

# Diverging ramp: dark-neutral at the center, green for above-center, red for below.
_NEUTRAL = np.array([40, 40, 40])
_GREEN = np.array([0, 150, 0])
_RED = np.array([175, 0, 0])


def _cell_style(value: float, center: float, vmax: float) -> Style:
    """Background color for a cell: distance from ``center`` (scaled by ``vmax``) drives
    intensity; sign picks the green (above) or red (below) arm of the ramp."""
    if vmax <= 0 or value != value:  # flat frame or NaN
        return Style(color="white", bgcolor="grey15")
    t = max(-1.0, min(1.0, (value - center) / vmax))
    end = _GREEN if t >= 0 else _RED
    rgb = (_NEUTRAL + abs(t) * (end - _NEUTRAL)).round().astype(int)
    return Style(color="white", bgcolor=f"rgb({rgb[0]},{rgb[1]},{rgb[2]})")


def _label(x: object) -> str:
    if hasattr(x, "strftime"):
        return x.strftime("%Y-%m")  # type: ignore[union-attr]
    return str(x)


def render_heatmap(
    df: pd.DataFrame,
    title: str,
    *,
    center: float = 0.0,
    fmt: str = "{:+.3f}",
    per_column: bool = False,
    label_width: int | None = None,
    console: Console | None = None,
) -> None:
    """Print ``df`` as a color-coded Rich table.

    ``per_column`` normalizes each column against its own mean/spread (use for the scorecard,
    whose metrics live on different scales); otherwise the whole frame shares one ``center``
    and a single max-absolute-deviation scale (use for correlation / IC-over-time / decay).
    ``label_width`` truncates row/column labels (with an ellipsis) — handy for long names
    like GICS sectors.
    """
    console = console or Console()
    vals = df.to_numpy(dtype=float)

    def lab(x: object) -> str:
        s = _label(x)
        return s if not label_width or len(s) <= label_width else s[: label_width - 1] + "…"

    if per_column:
        centers = np.nanmean(vals, axis=0)
        vmaxes = np.nanmax(np.abs(vals - centers), axis=0)
    else:
        centers = np.full(df.shape[1], center)
        dev = np.nanmax(np.abs(vals - center)) if np.isfinite(vals).any() else 0.0
        vmaxes = np.full(df.shape[1], dev)

    table = Table(title=title, title_style="bold", pad_edge=False, expand=False)
    table.add_column("", style="bold cyan", no_wrap=True)
    for col in df.columns:
        table.add_column(lab(col), justify="right", no_wrap=True)

    for r, (idx, row) in enumerate(df.iterrows()):
        cells: list[Text] = [Text(lab(idx), style="bold cyan")]
        for c, value in enumerate(row):
            text = "" if value != value else fmt.format(value)
            cells.append(Text(f" {text} ", style=_cell_style(float(value), centers[c], vmaxes[c])))
        table.add_row(*cells)

    console.print(table)
