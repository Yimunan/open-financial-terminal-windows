"""Unified factor catalog + signed-score builder.

One place that knows how to construct every screenable/backtestable factor and return its
*signed* cross-sectional scores (higher = more long), regardless of what data it needs:

* **price**   — Momentum / Volatility / Reversal (close panel only).
* **value/quality** — qhfi `ValueFactor`/`QualityFactor` fed from the real `FundamentalsStore`
  (PIT fundamentals via yfinance). Value = earnings yield (E/P); Quality = ROE.
* **alpha**   — a subset of the Alpha101 library over multi-field `MarketPanels`.

Both the screener and the backtester call `build_signed`, so they share one catalog.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from qhfi.core.types import AssetClass, DateRange, Panel, Universe
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.manager import DataManager
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider
from qhfi.factors.alpha101 import (
    Alpha004,
    Alpha006,
    Alpha012,
    Alpha013,
    Alpha033,
    Alpha101,
)
from qhfi.factors.library import (
    MomentumFactor,
    QualityFactor,
    ShortTermReversalFactor,
    ValueFactor,
    VolatilityFactor,
)
from qhfi.factors.market import MarketPanels

# kind: how the factor is built. label/direction: UI hints.
CATALOG: dict[str, dict] = {
    "momentum": {"label": "Momentum (12-1)", "kind": "price", "cls": MomentumFactor, "direction": "high=long"},
    "volatility": {"label": "Low Volatility", "kind": "price", "cls": VolatilityFactor, "direction": "low=long"},
    "reversal": {"label": "Short-term Reversal", "kind": "price", "cls": ShortTermReversalFactor, "direction": "low=long"},
    "value": {"label": "Value (E/P)", "kind": "value", "direction": "cheap=long"},
    "value_bp": {"label": "Value (B/P)", "kind": "value", "direction": "cheap=long"},
    "quality": {"label": "Quality (ROE)", "kind": "quality", "direction": "high=long"},
    "quality_gp": {"label": "Quality (Gross Margin)", "kind": "quality", "direction": "high=long"},
    "alpha101": {"label": "Alpha101 (intraday mom)", "kind": "alpha", "cls": Alpha101, "direction": "formula"},
    "alpha006": {"label": "Alpha006 (px/vol diverge)", "kind": "alpha", "cls": Alpha006, "direction": "formula"},
    "alpha012": {"label": "Alpha012 (vol reversal)", "kind": "alpha", "cls": Alpha012, "direction": "formula"},
    "alpha004": {"label": "Alpha004 (low reversal)", "kind": "alpha", "cls": Alpha004, "direction": "formula"},
    "alpha013": {"label": "Alpha013 (px/vol cov)", "kind": "alpha", "cls": Alpha013, "direction": "formula"},
    "alpha033": {"label": "Alpha033 (open/close)", "kind": "alpha", "cls": Alpha033, "direction": "formula"},
    # Multi-factor composites: cross-sectional z-score blends of their components (the standard
    # way to stack alpha — Asness's "value & momentum everywhere"). Equity-only when a component
    # needs fundamentals (the guard fires through the recursive build).
    "value_momentum": {"label": "Value + Momentum", "kind": "composite", "direction": "high=long",
                       "components": ["value", "momentum"]},
    "quality_momentum": {"label": "Quality + Momentum", "kind": "composite", "direction": "high=long",
                         "components": ["quality", "momentum"]},
}

# Factors that need a fundamentals panel (and therefore a network fetch on first use).
# value = earnings yield (E/P from eps_ttm); value_bp = book yield (B/P from book value per
# share) — the Fama-French HML definition; quality = ROE; quality_gp = gross profitability
# (Novy-Marx), the canonical alternative quality signal.
_FUNDAMENTAL_METRIC = {
    "value": "eps_ttm",
    "value_bp": "book_value_per_share",
    "quality": "roe",
    "quality_gp": "gross_margin",
}


def available_factors() -> list[dict]:
    return [
        {"key": k, "label": v["label"], "kind": v["kind"], "direction": v["direction"]}
        for k, v in CATALOG.items()
    ]


def trial_keys() -> list[str]:
    """Cheap, always-computable factors used as the search trials for Deflated Sharpe
    (no extra network round-trip)."""
    return [k for k, v in CATALOG.items() if v["kind"] in ("price", "alpha")]


def _align_pit(panel: Panel, prices: Panel) -> Panel:
    """Forward-fill a sparse PIT fundamentals panel onto the daily price grid. Done here (not
    only inside the qhfi factor) so knowable dates that aren't trading days still propagate."""
    if panel.empty:
        return pd.DataFrame(index=prices.index, columns=prices.columns, dtype=float)
    return panel.reindex(prices.index.union(panel.index)).ffill().reindex(prices.index)


def ensure_fundamentals(
    fstore: FundamentalsStore,
    fprov: YFinanceFundamentalsProvider,
    universe: Universe,
    metric: str,
    years: int = 4,
) -> None:
    """Populate the fundamentals lake for ``metric`` across the universe's equities (cached)."""
    end = date.today()
    span = DateRange(start=end - timedelta(days=int(years * 365.25)), end=end)
    for ins in universe.instruments:
        if ins.asset_class != AssetClass.EQUITY or fstore.has(ins, metric):
            continue
        try:
            series = fprov.fetch(ins, metric, span)
            if not series.empty:
                fstore.save(ins, metric, series)
        except Exception:  # noqa: BLE001 - a single bad ticker shouldn't sink the batch
            continue


def _zscore_rows(panel: Panel) -> Panel:
    """Cross-sectional (per-date) z-score: subtract the row mean, divide by the row std. Puts
    factors with different scales on a common footing before blending."""
    mu = panel.mean(axis=1)
    sd = panel.std(axis=1)
    return panel.sub(mu, axis=0).div(sd.where(sd > 0), axis=0)


def build_signed(
    dm: DataManager,
    fstore: FundamentalsStore,
    fprov: YFinanceFundamentalsProvider,
    universe: Universe,
    prices: Panel,
    factor_key: str,
) -> Panel:
    """Return the signed (direction-adjusted) score panel for ``factor_key`` (higher = long)."""
    if factor_key not in CATALOG:
        raise ValueError(f"unknown factor '{factor_key}'")
    spec = CATALOG[factor_key]
    kind = spec["kind"]

    if kind == "price":
        return spec["cls"]().signed(prices, universe)

    if kind == "alpha":
        panels = MarketPanels.from_store(dm.store, universe)
        return spec["cls"](panels).signed(prices, universe)

    if kind == "composite":
        # Cross-sectional z-score each component (already direction-signed, higher = long) and
        # average per cell over the components that have data. A fundamental component on a
        # non-equity universe raises through the recursive call below.
        zs = [
            _zscore_rows(build_signed(dm, fstore, fprov, universe, prices, ck))
            for ck in spec["components"]
        ]
        acc = None
        cnt = None
        for z in zs:
            z = z.reindex(index=prices.index, columns=prices.columns)
            mask = z.notna().astype(float)
            acc = z.fillna(0.0) if acc is None else acc + z.fillna(0.0)
            cnt = mask if cnt is None else cnt + mask
        return acc / cnt.where(cnt > 0)

    # value / quality — fundamentals-backed. These need issuer financials, which only equities
    # have; on an all-crypto/fx/commodity universe the panel would be empty and the backtest would
    # silently return a flat, holding-less curve. Fail loudly instead so the user gets a reason.
    if not any(i.asset_class == AssetClass.EQUITY for i in universe.instruments):
        raise ValueError(
            f"factor '{factor_key}' needs equity fundamentals; this universe has none "
            "(try a price factor like momentum, low-volatility or reversal)"
        )
    metric = _FUNDAMENTAL_METRIC[factor_key]
    ensure_fundamentals(fstore, fprov, universe, metric)
    raw = fstore.panel(universe.instruments, metric)
    aligned = _align_pit(raw, prices)
    if factor_key in ("value", "value_bp"):
        # E/P (eps_ttm) or B/P (book value per share) divided by price → a yield where
        # higher = cheaper = long. Same ValueFactor wrapper, different fundamental numerator.
        yield_panel = aligned / prices
        return ValueFactor(yield_panel).signed(prices, universe)
    return QualityFactor(aligned).signed(prices, universe)
