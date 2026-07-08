"""Shared mechanics for mean-reversion spread strategies (pairs, butterfly, …).

A spread strategy reduces a basket to one stationary series, normalizes it to a z-score, and
trades its reversion with an entry/exit band. The hysteresis state machine that turns the
z-score into a held position is identical across baskets, so it lives here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def hysteresis_positions(
    z: pd.Series, entry_z: float, exit_z: float, warmup: int = 0
) -> pd.Series:
    """Turn a spread z-score into a carried position in {-1, 0, +1} (the sign of the spread held;
    +1 = long the spread). Open when ``|z|`` exceeds ``entry_z``, close once z reverts back through
    ``exit_z`` toward the mean; the position is held between signals. The first ``warmup`` rows
    (and any non-finite z) never open a position. Causal — row t uses only z_t."""
    zv = z.to_numpy()
    out = np.zeros(len(zv), dtype=int)
    state = 0
    for i in range(len(zv)):
        zt = zv[i]
        if i >= warmup and np.isfinite(zt):
            if state == 0:
                if zt > entry_z:
                    state = -1                       # spread rich → short it
                elif zt < -entry_z:
                    state = 1                        # spread cheap → long it
            elif state == 1 and zt >= -exit_z:       # long spread reverted up to the mean
                state = 0
            elif state == -1 and zt <= exit_z:       # short spread reverted down to the mean
                state = 0
        out[i] = state
    return pd.Series(out, index=z.index)


def scale_to_gross(raw: pd.DataFrame, gross: float) -> pd.DataFrame:
    """Row-normalize raw leg weights so each active row's gross exposure (sum of |weights|) is
    ``gross``; all-zero rows stay flat."""
    denom = raw.abs().sum(axis=1)
    norm = (gross / denom).where(denom > 0, 0.0)
    return raw.mul(norm, axis=0)
