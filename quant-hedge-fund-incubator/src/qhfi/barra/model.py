"""BarraRiskModel — a cross-sectional fundamental factor risk model.

The Barra recipe, every period:

1. **Exposures** ``X`` (N×K): standardized style factors + industry dummies (``barra.exposures``).
2. **Factor returns** ``f_t``: a cross-sectional **WLS** regression of that day's asset returns on
   ``X`` (weights ∝ √cap, so big names anchor the fit). The residuals are the **specific**
   (stock-idiosyncratic) returns ``u_t``.
3. **Risk**: an EWMA covariance ``F`` (K×K) of the factor-return time series and an EWMA specific
   variance ``Δ`` (per name) of the residuals.
4. **Asset covariance** ``Σ = X F Xᵀ + diag(Δ)`` — a low-rank-plus-diagonal estimate that stays
   well-conditioned at N≫T and decomposes any portfolio's risk into factor vs specific.

This is the standard structure behind commercial equity risk models; here the cap/Size inputs use
dollar ADV (the lake has no market cap). The fitted object is small + picklable → versions in the
``ModelRepository`` under ``ModelDomain.RISK``.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd

from qhfi.barra.exposures import cap_proxy, industry_dummies, style_exposures
from qhfi.core.types import Panel, Universe
from qhfi.factors.market import MarketPanels

_ANN = 252.0


class BarraRiskModel:
    """Fit factor returns + covariance from exposures, then forecast portfolio risk."""

    def __init__(
        self,
        factor_halflife: int = 252,
        specific_halflife: int = 126,
        min_names: int = 10,
        keep_exposure_history: bool = True,
    ) -> None:
        self.factor_halflife = factor_halflife
        self.specific_halflife = specific_halflife
        self.min_names = min_names
        # Retain the per-date exposure matrix so realized return can be attributed to factor bets
        # (return_attribution / brinson_attribution). Costs ~T·N·K floats — turn off for huge N.
        self.keep_exposure_history = keep_exposure_history

    # ── fitting ───────────────────────────────────────────────────────────────
    def fit(
        self,
        returns: Panel,
        exposures: dict[str, Panel],
        industries: pd.DataFrame,
        cap: Panel,
    ) -> BarraRiskModel:
        """Estimate factor returns (per-date WLS), then EWMA factor cov + specific variance.

        ``returns``: asset returns (T×N). ``exposures``: standardized style panels. ``industries``:
        (N×n_ind) 0/1 dummies. ``cap``: cap proxy (T×N) for the √cap regression weights.
        """
        style_names = list(exposures)
        ind = industries.reindex(returns.columns).fillna(0.0)
        self.style_names_ = style_names
        self.industry_names_ = list(ind.columns)
        self.factor_names_ = style_names + list(ind.columns)
        k = len(self.factor_names_)
        ind_arr = ind.to_numpy(dtype=float)

        f_rows: list[np.ndarray] = []
        f_index: list[pd.Timestamp] = []
        spec = pd.DataFrame(np.nan, index=returns.index, columns=returns.columns)
        exp_hist: dict[pd.Timestamp, pd.DataFrame] = {}
        last_date = None

        for t in returns.index:
            r = returns.loc[t].to_numpy(dtype=float)
            styles = np.column_stack(
                [exposures[s].loc[t].to_numpy(dtype=float) for s in style_names]
            )
            x_full = np.column_stack([styles, ind_arr])                  # (N × K)
            w = cap.loc[t].to_numpy(dtype=float)
            mask = np.isfinite(r) & np.isfinite(x_full).all(axis=1) & np.isfinite(w) & (w > 0)
            if int(mask.sum()) < self.min_names:
                continue

            xm, rm = x_full[mask], r[mask]
            sw = np.sqrt(w[mask])
            keep = x_full[mask].any(axis=0)                              # drop empty industry cols
            b_keep, *_ = np.linalg.lstsq(xm[:, keep] * sw[:, None], rm * sw, rcond=None)
            f = np.full(k, np.nan)
            f[keep] = b_keep
            f_rows.append(f)
            f_index.append(t)
            spec.loc[t, returns.columns[mask]] = rm - xm[:, keep] @ b_keep
            if self.keep_exposure_history:
                exp_hist[t] = pd.DataFrame(xm, index=returns.columns[mask], columns=self.factor_names_)
            last_date = t

        if not f_rows:
            raise ValueError("no cross-section had enough names to estimate factor returns")

        self.factor_returns_ = pd.DataFrame(f_rows, index=f_index, columns=self.factor_names_)
        self.specific_returns_ = spec

        fr = self.factor_returns_.fillna(0.0)
        cov = fr.ewm(halflife=self.factor_halflife).cov()
        self.factor_cov_ = cov.loc[fr.index[-1]].reindex(
            index=self.factor_names_, columns=self.factor_names_
        ).fillna(0.0)
        sv = spec.pow(2).ewm(halflife=self.specific_halflife).mean().iloc[-1]
        self.specific_var_ = sv.fillna(float(np.nanmedian(sv.to_numpy())))
        self.exposures_history_ = exp_hist if self.keep_exposure_history else None
        self._store_exposures(exposures, ind, cap, last_date)
        return self

    @classmethod
    def from_panels(
        cls,
        panels: MarketPanels,
        universe: Universe,
        *,
        factor_halflife: int = 252,
        specific_halflife: int = 126,
        min_names: int = 10,
    ) -> BarraRiskModel:
        """Convenience: build exposures from ``MarketPanels`` and fit in one call."""
        if panels.close is None or panels.close.empty:
            # Fail with an actionable message — an empty panel otherwise dies deep inside pandas
            # ("no types given" from quantile on a zero-column frame).
            raise ValueError("empty market panels — no bars in the store for this universe")
        exposures = style_exposures(panels)
        industries = industry_dummies(universe)
        model = cls(factor_halflife, specific_halflife, min_names)
        return model.fit(panels.returns, exposures, industries, cap_proxy(panels))

    def _store_exposures(self, exposures, industries, cap, t) -> None:
        style_names = list(exposures)
        styles = np.column_stack([exposures[s].loc[t].to_numpy(dtype=float) for s in style_names])
        x_full = np.column_stack([styles, industries.to_numpy(dtype=float)])
        w = cap.loc[t].to_numpy(dtype=float)
        mask = np.isfinite(x_full).all(axis=1) & np.isfinite(w) & (w > 0)
        self.exposures_ = pd.DataFrame(
            x_full[mask], index=industries.index[mask], columns=self.factor_names_
        )

    # ── risk forecasting ────────────────────────────────────────────────────────
    def _aligned(
        self, names: list[str] | None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
        names = list(self.exposures_.index) if names is None else names
        x = self.exposures_.reindex(names).dropna(how="any")
        names = list(x.index)
        f = self.factor_cov_.to_numpy()
        sv = self.specific_var_.reindex(names).fillna(self.specific_var_.median()).to_numpy()
        return x.to_numpy(), f, sv, names

    def covariance(self, names: list[str] | None = None) -> pd.DataFrame:
        """Asset covariance ``Σ = X F Xᵀ + diag(Δ)`` (daily) as a labelled (N×N) frame."""
        x, f, sv, names = self._aligned(names)
        sigma = x @ f @ x.T + np.diag(sv)
        return pd.DataFrame(sigma, index=names, columns=names)

    def factor_cov(self, annualize: bool = True) -> pd.DataFrame:
        return self.factor_cov_ * (_ANN if annualize else 1.0)

    def specific_var(self) -> pd.Series:
        return self.specific_var_

    def risk_decomposition(self, weights: pd.Series) -> dict[str, object]:
        """Decompose a portfolio's forecast risk into factor vs specific (annualized vols)."""
        x, f, sv, names = self._aligned(list(weights.index))
        w = weights.reindex(names).fillna(0.0).to_numpy()
        fe = x.T @ w                                            # portfolio factor exposures
        factor_var = float(fe @ f @ fe)
        specific_var = float((w**2 * sv).sum())
        total = factor_var + specific_var
        return {
            "total_vol": float(np.sqrt(max(total, 0.0) * _ANN)),
            "factor_vol": float(np.sqrt(max(factor_var, 0.0) * _ANN)),
            "specific_vol": float(np.sqrt(max(specific_var, 0.0) * _ANN)),
            "pct_factor": float(factor_var / total) if total > 0 else 0.0,
            "factor_exposures": pd.Series(fe, index=self.factor_names_),
        }

    def predict_vol(self, weights: pd.Series) -> float:
        """Annualized forecast volatility of a weight vector."""
        return cast(float, self.risk_decomposition(weights)["total_vol"])

    # ── attribution ───────────────────────────────────────────────────────────
    def risk_contributions(self, weights: pd.Series) -> pd.DataFrame:
        """Per-position marginal + component contribution to forecast risk (Euler allocation).

        ``mctr`` = ∂σ_p/∂wᵢ = (Σw)ᵢ/σ_p (annualized); ``cctr`` = wᵢ·mctrᵢ, the component each name
        owns — these sum **exactly** to the portfolio's total vol (Euler's theorem). ``pct`` is the
        scale-free share of risk per name (sums to 1). ``Σw`` is formed via the factor structure
        ``X F (Xᵀw) + Δ⊙w`` so it never materializes the full N×N matrix.
        """
        x, f, sv, names = self._aligned(list(weights.index))
        w = weights.reindex(names).fillna(0.0).to_numpy()
        sigma_w = x @ (f @ (x.T @ w)) + sv * w
        vol = float(np.sqrt(max(w @ sigma_w, 0.0)))
        ann = float(np.sqrt(_ANN))
        mctr = sigma_w / vol if vol > 0 else np.zeros_like(w)
        cctr = w * mctr
        return pd.DataFrame(
            {
                "weight": w,
                "mctr": mctr * ann,
                "cctr": cctr * ann,
                "pct": cctr / vol if vol > 0 else np.zeros_like(w),
            },
            index=names,
        )

    def factor_risk_contributions(self, weights: pd.Series) -> pd.DataFrame:
        """Decompose factor variance across each style/industry factor (Euler on factor variance).

        Component j is ``feⱼ·(F·fe)ⱼ`` and the columns sum to the **factor** variance, so
        ``pct_total`` (share of *total* portfolio variance, specific included) sums to ``pct_factor``
        rather than 1. ``var_contribution`` is annualized variance.
        """
        x, f, sv, names = self._aligned(list(weights.index))
        w = weights.reindex(names).fillna(0.0).to_numpy()
        fe = x.T @ w
        contrib = fe * (f @ fe)                                  # per-factor component of factor var
        total = float(contrib.sum()) + float((w**2 * sv).sum())
        return pd.DataFrame(
            {
                "exposure": fe,
                "var_contribution": contrib * _ANN,
                "pct_total": contrib / total if total > 0 else np.zeros_like(contrib),
            },
            index=self.factor_names_,
        )

    def factor_return_attribution(self, compound: bool = False) -> pd.DataFrame:
        """Cumulative realized factor returns over the fit window — what each factor *paid*.

        Sum (``compound=False``) or geometric-compound (``True``) the per-date factor-return series.
        Combined with a book's exposures (``Xᵀw``) this attributes realized return to factor bets.
        """
        fr = self.factor_returns_.fillna(0.0)
        return (1.0 + fr).cumprod() - 1.0 if compound else fr.cumsum()

    # ── return attribution (needs the exposure history) ─────────────────────────
    def return_attribution(self, weights: pd.Series | pd.DataFrame) -> pd.DataFrame:
        """Attribute a book's *realized* return day-by-day to each factor bet + stock selection.

        Uses the stored exposure history: on each date the cross-sectional regression makes the
        identity ``r_i = Σ_k X_{i,k} f_k + u_i`` hold exactly, so the portfolio's realized return
        decomposes as ``r_p = Σ_k (Xᵀw)_k · f_k + Σ_i w_i u_i`` — i.e. **factor-exposure × factor-
        return P&L** plus a **specific** (selection) residual. ``weights`` is either a static
        ``Series`` (held every date) or a ``DataFrame`` history (row ``t`` = weights into date ``t``).

        Returns a per-date frame with one column per factor, ``specific``, and ``total`` (their sum,
        = the book's realized return over the names in that day's cross-section). Sum/cumsum it for
        the contribution over the window. Requires ``keep_exposure_history=True`` at fit time.
        """
        if not getattr(self, "exposures_history_", None):
            raise ValueError("no exposure history — refit with keep_exposure_history=True")
        is_frame = isinstance(weights, pd.DataFrame)
        cols = list(self.factor_names_)
        rows: dict[pd.Timestamp, dict[str, float]] = {}
        for t, x in self.exposures_history_.items():
            names = x.index
            w = (weights.loc[t] if (is_frame and t in weights.index) else
                 None if is_frame else weights)
            if w is None:
                continue
            w = w.reindex(names).fillna(0.0).to_numpy()
            f = self.factor_returns_.loc[t].reindex(cols).fillna(0.0).to_numpy()
            u = self.specific_returns_.loc[t].reindex(names).fillna(0.0).to_numpy()
            fe = x.to_numpy().T @ w                              # portfolio factor exposures
            factor_pnl = fe * f
            spec_pnl = float(w @ u)
            row = dict(zip(cols, factor_pnl, strict=True))
            row["specific"] = spec_pnl
            row["total"] = float(factor_pnl.sum()) + spec_pnl
            rows[t] = row
        out = pd.DataFrame.from_dict(rows, orient="index", columns=cols + ["specific", "total"])
        out.index.name = "date"
        return out.sort_index()

    def brinson_attribution(
        self,
        port_weights: pd.Series,
        bench_weights: pd.Series,
        asset_returns: pd.Series | None = None,
        date: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Single-period Brinson–Fachler active-return attribution by GICS sector.

        Splits the book's active return (vs a benchmark) into, per sector, **allocation**
        ``(wₚ−w_b)(r_b,s−r_b)`` (over/under-weighting a sector vs how it did), **selection**
        ``w_b(rₚ,s−r_b,s)`` (picking better names within it), and their **interaction**. Sectors are
        read from the model's industry dummies; ``asset_returns`` defaults to the realized returns
        reconstructed from the exposure history at ``date`` (latest if omitted). The three columns
        sum (over sectors) to the total active return ``rₚ−r_b`` when both weight vectors sum to 1.
        """
        sectors = self.exposures_[self.industry_names_].idxmax(axis=1)   # name → sector
        if asset_returns is None:
            hist = self.exposures_history_ or {}
            t = date or (max(hist) if hist else self.factor_returns_.index[-1])
            x = (hist or {}).get(t)
            if x is None:
                raise ValueError("need exposure history or an explicit asset_returns for Brinson")
            f = self.factor_returns_.loc[t].reindex(self.factor_names_).fillna(0.0).to_numpy()
            u = self.specific_returns_.loc[t].reindex(x.index).fillna(0.0)
            asset_returns = pd.Series(x.to_numpy() @ f, index=x.index) + u

        names = sectors.index.intersection(asset_returns.index)
        df = pd.DataFrame({
            "sector": sectors.reindex(names),
            "wp": port_weights.reindex(names).fillna(0.0),
            "wb": bench_weights.reindex(names).fillna(0.0),
            "r": asset_returns.reindex(names).fillna(0.0),
        })
        df["wpr"], df["wbr"] = df["wp"] * df["r"], df["wb"] * df["r"]
        g = df.groupby("sector")
        wp, wb = g["wp"].sum(), g["wb"].sum()
        rp = (g["wpr"].sum() / wp.replace(0.0, np.nan)).fillna(0.0)      # sector port/bench returns
        rb = (g["wbr"].sum() / wb.replace(0.0, np.nan)).fillna(0.0)
        rb_total = float(df["wbr"].sum() / df["wb"].sum()) if df["wb"].sum() else 0.0

        allocation = (wp - wb) * (rb - rb_total)
        selection = wb * (rp - rb)
        interaction = (wp - wb) * (rp - rb)
        return pd.DataFrame({
            "w_port": wp, "w_bench": wb, "r_port": rp, "r_bench": rb,
            "allocation": allocation, "selection": selection, "interaction": interaction,
            "total": allocation + selection + interaction,
        })


def bias_statistic(
    realized_returns: pd.Series, predicted_vol: pd.Series, window: int = 21
) -> pd.Series:
    """Rolling bias statistic — the canonical risk-model calibration check.

    The standardized return ``z_t = r_t / σ̂_t`` (realized over predicted *daily* vol) should be
    ~N(0,1) if the forecast is well calibrated, so a rolling std of ``z`` near **1.0** means the
    model is neither over- nor under-forecasting risk (>1 under-forecasts, <1 over-forecasts).
    """
    z = realized_returns / predicted_vol.replace(0.0, np.nan)
    return z.rolling(window).std(ddof=0)
