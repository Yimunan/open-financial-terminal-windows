"""Alpha selection & weighting — prune correlated signals, then weight survivors.

* ``vif_prune`` — iterative variance-inflation-factor elimination: drop the most collinear
  signal, repeat until all remaining VIFs are below a threshold (the FactSet/standard
  procedure the research confirmed).
* ``ic_weights`` — IC-IR weighting (ICmean/std-of-IC) over already-standardized signals.
  NOTE: computed in-sample here (static) — applying it to the same period is mild look-ahead;
  for OOS use a trailing window. Per the research, IC-weighting's *superiority* over
  equal-weight is unproven, so treat equal-weight as the honest default.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import Panel
from qhfi.factors.evaluation import ic_summary, information_coefficient


def _vif(matrix: np.ndarray, j: int) -> float:
    y = matrix[:, j]
    others = np.delete(matrix, j, axis=1)
    x = np.column_stack([np.ones(len(others)), others])
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    if ss_tot <= 0:
        return float("inf")
    r2 = 1 - ss_res / ss_tot
    return float("inf") if r2 >= 1 else 1.0 / (1.0 - r2)


def vif_prune(signals: dict[str, Panel], threshold: float = 5.0) -> list[str]:
    """Return the names of signals to keep after iterative VIF elimination."""
    pooled = pd.DataFrame({name: panel.stack() for name, panel in signals.items()}).dropna()
    cols = list(pooled.columns)
    while len(cols) > 1:
        mat = pooled[cols].to_numpy()
        vifs = {c: _vif(mat, i) for i, c in enumerate(cols)}
        worst = max(vifs, key=lambda k: vifs[k])
        if vifs[worst] < threshold:
            break
        cols.remove(worst)
    return cols


def ic_weights(signals: dict[str, Panel], prices: Panel, horizon: int = 5) -> dict[str, float]:
    """IC-IR weights, normalized so the absolute weights sum to 1 (signs preserved)."""
    raw = {
        name: ic_summary(information_coefficient(sig, prices, horizon=horizon)).ic_ir
        for name, sig in signals.items()
    }
    total = sum(abs(v) for v in raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}
