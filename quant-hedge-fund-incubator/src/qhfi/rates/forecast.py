"""Forecast forward curve moves from the term-structure factors.

A time-series (not cross-sectional) model: features are today's curve factors — level / slope /
curvature and their recent momentum, plus carry+roll — and the label is the realized change of
a chosen point on the curve (e.g. the 10Y yield, or the 2s10s slope) ``horizon`` days ahead.
The fitted sklearn estimator is versioned in the taxonomy-aware ``ModelRepository`` under
``domain=CURVE / asset_class=RATES``.

Look-ahead-free by construction: features at *t* use only the curve through *t*; the label is
the strictly-forward change, dropped where undefined.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qhfi.core.types import AssetClass
from qhfi.models.card import ModelCard, ModelDomain
from qhfi.models.predictive import ModelSpec, build_estimator
from qhfi.models.repository import ModelRepository
from qhfi.rates.curve import carry_rolldown, curve_metrics


def curve_features(
    curve: pd.DataFrame, target_tenor: str = "10Y", horizon: int = 21
) -> pd.DataFrame:
    """Assemble the per-date feature frame: level/slope/curvature + their ``horizon``-day
    momentum + carry/roll-down of the target tenor."""
    m = curve_metrics(curve)
    mom = m.diff(horizon).add_suffix("_mom")
    cr = carry_rolldown(curve, target_tenor, horizon)[["carry_roll"]]
    return pd.concat([m, mom, cr], axis=1)


def forward_change(curve: pd.DataFrame, target: str = "10Y", horizon: int = 21) -> pd.Series:
    """Realized change of the target point ``horizon`` days ahead, aligned to the decision date.

    ``target`` is a tenor column ('10Y') or a curve_metrics field ('slope', 'level', 'curvature').
    """
    is_metric = target in ("level", "slope", "curvature")
    series = curve_metrics(curve)[target] if is_metric else curve[target]
    return series.shift(-horizon) - series


def _xy(curve: pd.DataFrame, target: str, horizon: int) -> tuple[pd.DataFrame, pd.Series]:
    feats = curve_features(curve, _tenor_of(target), horizon)
    y = forward_change(curve, target, horizon)
    frame = feats.assign(__y__=y).dropna(how="any")
    return frame.drop(columns="__y__"), frame["__y__"]


def _tenor_of(target: str) -> str:
    """The tenor whose carry/roll to include as a feature (10Y for curve-metric targets)."""
    return "10Y" if target in ("level", "slope", "curvature") else target


def train_curve_forecaster(
    curve: pd.DataFrame, spec: ModelSpec, *, target: str = "10Y", horizon: int = 21
) -> tuple[Any, dict[str, float], list[str]]:
    """Fit ``spec`` to forecast the forward change of ``target``. Returns
    ``(estimator, metrics, feature_names)`` with in-sample R² and rank IC."""
    X, y = _xy(curve, target, horizon)
    estimator = build_estimator(spec)
    estimator.fit(X.to_numpy(dtype=float), y.to_numpy(dtype=float))
    pred = pd.Series(estimator.predict(X.to_numpy(dtype=float)), index=y.index)
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    ic = float(pred.corr(y, method="spearman"))
    metrics = {"r2": r2, "ic": ic if not np.isnan(ic) else 0.0, "n": float(len(y))}
    return estimator, metrics, list(X.columns)


def train_and_save_curve_forecaster(
    repo: ModelRepository,
    name: str,
    curve: pd.DataFrame,
    spec: ModelSpec,
    *,
    target: str = "10Y",
    horizon: int = 21,
    tags: list[str] | None = None,
) -> ModelCard:
    """Train then version the curve forecaster under ``CURVE / RATES`` in the repository."""
    estimator, metrics, feature_names = train_curve_forecaster(
        curve, spec, target=target, horizon=horizon
    )
    span = (str(curve.index.min().date()), str(curve.index.max().date()))
    return repo.save(
        name,
        estimator,
        framework="sklearn",
        domain=ModelDomain.CURVE,
        asset_class=AssetClass.RATES,
        params={"spec": spec.model_dump(), "target": target, "horizon": horizon},
        features=feature_names,
        train_span=span,
        metrics=metrics,
        lineage={"source": "treasury_curve", "target": target, "horizon": horizon},
        tags=tags or [],
    )
