"""Tests for the Barra risk model: exposures, factor-return recovery, the asset covariance and
risk decomposition, the bias statistic, and the BarraMinVarStrategy + repository round-trip.

Synthetic, offline, seeded — returns are generated from a known factor structure so the
cross-sectional regression must recover it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.engine import BacktestEngine
from qhfi.barra.exposures import industry_dummies, style_exposures
from qhfi.barra.model import BarraRiskModel, bias_statistic
from qhfi.core.types import AssetClass, EquityMeta, Instrument, Universe
from qhfi.factors.market import MarketPanels
from qhfi.models.card import ModelDomain
from qhfi.models.repository import ModelRepository
from qhfi.strategy.library.barra_minvar import BarraMinVarParams, BarraMinVarStrategy


def _universe(ids: list[str], sectors: list[str]) -> Universe:
    return Universe(name="t", instruments=[
        Instrument(id=i, asset_class=AssetClass.EQUITY, exchange="x",
                   equity=EquityMeta(gics_sector=s)) for i, s in zip(ids, sectors, strict=True)
    ])


@pytest.fixture
def panels_universe() -> tuple[MarketPanels, Universe]:
    rng = np.random.default_rng(4)
    n, t = 12, 600
    dates = pd.date_range("2019-01-01", periods=t, freq="B", tz="UTC")
    ids = [f"A{i}" for i in range(n)]
    sectors = [["Tech", "Health", "Financials"][i % 3] for i in range(n)]
    rets = rng.normal(0.0004, 0.015, (t, n))
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=dates, columns=ids)
    vol = pd.DataFrame(rng.uniform(1e6, 5e6, (t, n)), index=dates, columns=ids)
    panels = MarketPanels(open=close, high=close, low=close, close=close, volume=vol)
    return panels, _universe(ids, sectors)


# ── exposures ─────────────────────────────────────────────────────────────────
def test_style_exposures_are_standardized(panels_universe):
    panels, _ = panels_universe
    exps = style_exposures(panels)
    assert set(exps) == {"size", "beta", "momentum", "resid_vol", "reversal"}
    last = exps["momentum"].iloc[-1].dropna()
    assert abs(last.mean()) < 1e-9 and abs(last.std(ddof=0) - 1.0) < 1e-6


def test_industry_dummies_one_hot(panels_universe):
    _, uni = panels_universe
    d = industry_dummies(uni)
    assert set(d.columns) == {"Tech", "Health", "Financials"}
    assert (d.sum(axis=1) == 1.0).all()                       # each name in exactly one sector


# ── factor-return recovery (the regression) ─────────────────────────────────────
def test_fit_recovers_known_factor_returns():
    rng = np.random.default_rng(1)
    n, t, n_style, n_ind = 30, 90, 3, 3
    dates = pd.date_range("2020-01-01", periods=t, freq="B", tz="UTC")
    ids = [f"A{i}" for i in range(n)]
    style_names = [f"s{j}" for j in range(n_style)]
    exposures = {s: pd.DataFrame(rng.standard_normal((t, n)), index=dates, columns=ids)
                 for s in style_names}
    sectors = [f"ind{i % n_ind}" for i in range(n)]
    industries = industry_dummies(_universe(ids, sectors))
    ind_arr = industries.reindex(ids).to_numpy()

    f_true = rng.standard_normal((t, n_style + n_ind)) * 0.01
    returns = pd.DataFrame(0.0, index=dates, columns=ids)
    for k, day in enumerate(dates):
        x = np.column_stack([exposures[s].loc[day] for s in style_names] + [ind_arr])
        returns.loc[day] = x @ f_true[k]

    cap = pd.DataFrame(1.0, index=dates, columns=ids)         # equal-weight (OLS)
    model = BarraRiskModel(min_names=10).fit(returns, exposures, industries, cap)
    np.testing.assert_allclose(model.factor_returns_.to_numpy(), f_true, atol=1e-6)


# ── covariance + risk decomposition ─────────────────────────────────────────────
def test_covariance_is_symmetric_psd(panels_universe):
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    sigma = model.covariance().to_numpy()
    assert np.allclose(sigma, sigma.T)
    assert np.linalg.eigvalsh(sigma).min() > -1e-12          # PSD (diag specific keeps it PD)


def test_risk_decomposition_adds_up(panels_universe):
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    names = list(model.exposures_.index)
    w = pd.Series(1.0 / len(names), index=names)
    rd = model.risk_decomposition(w)
    parts = rd["factor_vol"] ** 2 + rd["specific_vol"] ** 2
    assert parts == pytest.approx(rd["total_vol"] ** 2, rel=1e-6)
    assert 0.0 <= rd["pct_factor"] <= 1.0
    assert rd["total_vol"] == pytest.approx(model.predict_vol(w))


def test_risk_contributions_sum_to_total_vol(panels_universe):
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    names = list(model.exposures_.index)
    w = pd.Series(1.0 / len(names), index=names)
    rc = model.risk_contributions(w)
    total = model.predict_vol(w)
    assert rc["cctr"].sum() == pytest.approx(total, rel=1e-6)    # Euler: Σ cctr = σ_p
    assert rc["pct"].sum() == pytest.approx(1.0, rel=1e-6)


def test_factor_risk_contributions_match_factor_var(panels_universe):
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    names = list(model.exposures_.index)
    w = pd.Series(1.0 / len(names), index=names)
    frc = model.factor_risk_contributions(w)
    rd = model.risk_decomposition(w)
    # per-factor variance contributions sum to the aggregate factor variance
    assert frc["var_contribution"].sum() == pytest.approx(rd["factor_vol"] ** 2, rel=1e-6)
    assert frc["pct_total"].sum() == pytest.approx(rd["pct_factor"], rel=1e-6)


def test_factor_return_attribution_shapes(panels_universe):
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    cum = model.factor_return_attribution()
    assert list(cum.columns) == model.factor_names_
    # cumulative sum == final running total
    assert cum.iloc[-1].to_numpy() == pytest.approx(model.factor_returns_.fillna(0.0).sum().to_numpy())


def test_return_attribution_identity(panels_universe):
    """Per-date `total` equals the book's realized return over that day's cross-section."""
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    names = list(model.exposures_.index)
    w = pd.Series(1.0 / len(names), index=names)
    attr = model.return_attribution(w)
    assert list(attr.columns) == model.factor_names_ + ["specific", "total"]
    # factor columns + specific sum to total (decomposition is exact)
    recomposed = attr[model.factor_names_].sum(axis=1) + attr["specific"]
    np.testing.assert_allclose(recomposed.to_numpy(), attr["total"].to_numpy(), atol=1e-12)
    # total matches the independently-computed realized return over the covered names
    rets = panels.returns
    for t in list(attr.index)[:40]:
        cov = model.exposures_history_[t].index
        realized = float((w.reindex(cov).fillna(0.0) * rets.loc[t].reindex(cov)).sum())
        assert attr.loc[t, "total"] == pytest.approx(realized, abs=1e-9)


def test_return_attribution_recovers_factor_pnl():
    """With returns generated purely from factors (no specific), specific P&L ≈ 0."""
    rng = np.random.default_rng(2)
    n, t, n_style, n_ind = 30, 90, 3, 3
    dates = pd.date_range("2020-01-01", periods=t, freq="B", tz="UTC")
    ids = [f"A{i}" for i in range(n)]
    style_names = [f"s{j}" for j in range(n_style)]
    exposures = {s: pd.DataFrame(rng.standard_normal((t, n)), index=dates, columns=ids)
                 for s in style_names}
    sectors = [f"ind{i % n_ind}" for i in range(n)]
    industries = industry_dummies(_universe(ids, sectors))
    ind_arr = industries.reindex(ids).to_numpy()
    f_true = rng.standard_normal((t, n_style + n_ind)) * 0.01
    returns = pd.DataFrame(0.0, index=dates, columns=ids)
    for k, day in enumerate(dates):
        x = np.column_stack([exposures[s].loc[day] for s in style_names] + [ind_arr])
        returns.loc[day] = x @ f_true[k]
    cap = pd.DataFrame(1.0, index=dates, columns=ids)
    model = BarraRiskModel(min_names=10).fit(returns, exposures, industries, cap)

    w = pd.Series(1.0 / n, index=ids)
    attr = model.return_attribution(w)
    assert attr["specific"].abs().max() < 1e-8                 # all return is factor-driven
    np.testing.assert_allclose(
        attr["total"].to_numpy(), attr[model.factor_names_].sum(axis=1).to_numpy(), atol=1e-12)


def test_return_attribution_needs_history(panels_universe):
    panels, uni = panels_universe
    exposures = style_exposures(panels)
    from qhfi.barra.exposures import cap_proxy
    model = BarraRiskModel(keep_exposure_history=False).fit(
        panels.returns, exposures, industry_dummies(uni), cap_proxy(panels))
    with pytest.raises(ValueError, match="exposure history"):
        model.return_attribution(pd.Series(dtype=float))


def test_brinson_attribution_adds_to_active_return(panels_universe):
    """Allocation + selection + interaction sum to the active return rₚ − r_b."""
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    names = list(model.exposures_.index)
    rng = np.random.default_rng(7)
    r = pd.Series(rng.normal(0.0, 0.02, len(names)), index=names)
    port = pd.Series(rng.random(len(names)), index=names); port /= port.sum()
    bench = pd.Series(rng.random(len(names)), index=names); bench /= bench.sum()

    br = model.brinson_attribution(port, bench, asset_returns=r)
    assert set(br.index) == {"Tech", "Health", "Financials"}    # sectors from industry dummies
    active = float(br["total"].sum())
    expected = float((port * r).sum() - (bench * r).sum())
    assert active == pytest.approx(expected, abs=1e-12)
    # reconstructed-returns path (asset_returns=None) runs and is finite
    br2 = model.brinson_attribution(port, bench)
    assert np.isfinite(br2["total"].to_numpy()).all()


def test_bias_statistic_is_one_when_calibrated():
    rng = np.random.default_rng(9)
    sigma_daily = 0.01
    r = pd.Series(rng.normal(0.0, sigma_daily, 2000))
    pred = pd.Series(sigma_daily, index=r.index)
    assert bias_statistic(r, pred, window=60).mean() == pytest.approx(1.0, abs=0.1)


# ── strategy + repository ───────────────────────────────────────────────────────
def test_barra_minvar_weights_valid_and_backtests(panels_universe):
    panels, uni = panels_universe
    strat = BarraMinVarStrategy(panels, BarraMinVarParams(estimation_window=250, rebalance_days=21))
    w = strat.generate_weights(panels.close, uni)
    assert w.shape == panels.close.shape
    assert (w >= -1e-9).all().all()                           # long-only
    invested = w.sum(axis=1)
    active = invested[invested > 1e-9]
    assert len(active) > 0
    np.testing.assert_allclose(active.to_numpy(), 1.0, atol=1e-6)   # fully invested, gross 1
    result = BacktestEngine().run(w, panels.close, uni)
    assert len(result.equity_curve) == len(panels.close)


def test_barra_minvar_in_taxonomy_not_registered():
    from qhfi.strategy.registry import all_names
    from qhfi.strategy.taxonomy import Status, StrategyStyle, get
    kind = get("barra_minvar")
    assert kind.style is StrategyStyle.RISK_BASED and kind.status is Status.LIVE
    assert "barra_minvar" not in all_names()       # carries MarketPanels (not registered)


def test_risk_model_versioned_under_risk(tmp_path, panels_universe):
    panels, uni = panels_universe
    model = BarraRiskModel.from_panels(panels, uni)
    repo = ModelRepository(tmp_path)
    repo.save("barra-equity", model, framework="custom",
              domain=ModelDomain.RISK, asset_class=AssetClass.EQUITY)
    assert (tmp_path / "risk" / "equity" / "barra-equity" / "v1" / "model.pkl").exists()
    loaded, card = repo.load("barra-equity")
    assert card.domain is ModelDomain.RISK
    pd.testing.assert_frame_equal(loaded.covariance(), model.covariance())
