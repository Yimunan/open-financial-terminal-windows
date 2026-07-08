"""Tests for multi-factor composites and the z-score blend helper in `factors`.

`_zscore_rows` is pure; the catalog wiring is pure. Both run with no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import factors as fac


def test_zscore_rows_standardizes_each_date():
    idx = pd.date_range("2023-01-01", periods=2, freq="D")
    panel = pd.DataFrame([[1.0, 2.0, 3.0], [10.0, 20.0, 30.0]], index=idx, columns=["A", "B", "C"])
    z = fac._zscore_rows(panel)
    # Each row → mean 0, and the ranking is preserved (ascending).
    assert abs(float(z.iloc[0].mean())) < 1e-9
    assert z.iloc[0]["A"] < z.iloc[0]["B"] < z.iloc[0]["C"]
    # Pandas std is ddof=1: [1,2,3] → std 1 → z = [-1, 0, 1].
    assert abs(z.iloc[0]["A"] - (-1.0)) < 1e-9 and abs(z.iloc[0]["C"] - 1.0) < 1e-9


def test_zscore_constant_row_is_nan_not_inf():
    panel = pd.DataFrame([[5.0, 5.0, 5.0]], index=pd.date_range("2023-01-01", periods=1), columns=list("ABC"))
    z = fac._zscore_rows(panel)
    assert z.iloc[0].isna().all()  # zero variance → NaN, never inf


def test_composites_registered_with_components():
    for key in ("value_momentum", "quality_momentum"):
        spec = fac.CATALOG[key]
        assert spec["kind"] == "composite"
        assert len(spec["components"]) >= 2
        # every component is itself a real catalog factor
        assert all(c in fac.CATALOG for c in spec["components"])


def test_composites_listed_but_not_cheap_trials():
    keys = {f["key"] for f in fac.available_factors()}
    assert {"value_momentum", "quality_momentum"} <= keys
    # composites pull fundamentals via a component → not free DSR trials
    assert "value_momentum" not in fac.trial_keys()
