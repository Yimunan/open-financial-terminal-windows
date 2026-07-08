# Risk Attribution — Barra Cross-Sectional Factor Model

How QHFI forecasts portfolio risk and attributes it to **common factors** vs **stock-specific**
noise. The engine is a Barra-style cross-sectional fundamental factor model: rather than estimate a
full N×N asset covariance directly (rank-deficient and noisy at N≫T), it explains every stock's
return through a handful of factors plus an idiosyncratic residual, producing a structured,
always-invertible covariance

```
Σ = X F Xᵀ + diag(Δ)
```

| Symbol | Shape | Meaning |
| --- | --- | --- |
| `X` | N×K | each stock's **exposures** to K factors (the design matrix) |
| `F` | K×K | **factor covariance** — how factors co-move |
| `Δ` | N | each stock's **specific** (idiosyncratic) variance |

The low-rank-plus-diagonal form is what makes attribution clean: any portfolio's variance splits
exactly into a factor piece and a specific piece.

## The recipe

Every period:

1. **Exposures** `X`: standardized style factors + industry dummies.
2. **Factor returns** `f_t`: a per-date cross-sectional **WLS** regression of that day's asset
   returns on `X` (weights ∝ √cap). Residuals are the **specific returns** `u_t`.
3. **Risk**: EWMA covariance `F` of the factor-return series; EWMA specific variance `Δ` of the
   residuals.
4. **Asset covariance** `Σ = X F Xᵀ + diag(Δ)` → decomposes any portfolio into factor vs specific.

### 1. Exposures `X`

Two kinds of columns, all causal (date `t` uses only data through `t`):

**Five style factors** — derived from price/volume only, so coverage is complete:

| Factor | Definition |
| --- | --- |
| `size` | `log(dollar ADV)` — ADV is the market-cap proxy (lake has no cap) |
| `beta` | rolling 252-day CAPM beta vs an equal-weight market |
| `momentum` | 12-1 return: `close.shift(21)/close.shift(273) − 1` (skip most recent month) |
| `resid_vol` | 63-day std of market-residual return `r − β·r_m` (idiosyncratic-risk style) |
| `reversal` | trailing 1-month return (short-term reversal) |

Each is **cross-sectionally standardized** every date (winsorize 2% → z-score), so columns are
mean≈0 / std≈1 and the estimated factor *returns* are comparable in units.

**Industry dummies** — one-hot GICS-sector membership. Together the sector dummies span the market
intercept, so the model deliberately omits a separate constant. `K = 5 styles + #GICS sectors`.

### 2. Factor returns by cross-sectional WLS

For **each date `t`**, one regression *across stocks*:

```
r_i,t = Σ_k  X_i,k · f_k,t  +  u_i,t
```

- Returns on the left; known exposures × **unknown factor returns** `f_t` on the right.
- Solved by **WLS with weights ∝ √cap**, so large/liquid names anchor the fit.
- **Residuals** `u_t = r − X·f` are the **specific returns**.

Guards: mask NaN returns/exposures and zero-cap names; skip any date with fewer than `min_names=10`
valid stocks; drop empty industry columns before solving to keep the design matrix full rank.
Iterating daily yields `factor_returns_` (T×K) and `specific_returns_` (T×N).

### 3. From histories to a risk model

Both estimators are **EWMA** (recent data weighted more):

- **Factor covariance `F`** = EWMA covariance of the factor-return series, **halflife 252d**.
- **Specific variance `Δ`** = EWMA mean of squared residuals, **halflife 126d**; missing names get
  the cross-sectional median.

Specific risk decays faster (126d) than factor risk (252d) — idiosyncratic vol is more
regime-dependent. The latest exposure row per name is stored so the model can forecast forward.

### 4. The attribution

`risk_decomposition(weights)` — given portfolio weights `w`:

1. **Portfolio factor exposures** `fe = Xᵀ w` — net loading on each factor ("+0.4 momentum, −0.2
   size"). This is the *exposure attribution*: which bets the book carries.
2. **Factor variance** `fe · F · fe` — risk from common factor moves.
3. **Specific variance** `Σ wᵢ²·Δᵢ` — stock-specific noise; no cross terms (residuals assumed
   uncorrelated → the `diag(Δ)`).
4. Returns annualized vols (×√252) for `total`, `factor`, `specific`, plus `pct_factor` (systematic
   fraction of variance) and the full `factor_exposures` series.

Decomposition identity: `factor_var + specific_var = total_var`, i.e.
`factor_vol² + specific_vol² = total_vol²`.

### 5. Return attribution (realized P&L, not risk)

`fit()` also retains the **exposure history** (`exposures_history_`, one `X_t` per date — toggle
with `keep_exposure_history`). Because each day's cross-sectional regression makes
`r_i = Σ_k X_{i,k} f_k + u_i` hold *exactly*, a book's realized return decomposes cleanly:

```
r_p,t = Σ_k (Xᵀ w)_{k,t} · f_{k,t}  +  Σ_i w_{i,t} · u_{i,t}
        └── factor-exposure × factor-return ──┘     └── specific (selection) ──┘
```

- `return_attribution(weights)` — per-date P&L, one column per factor + `specific` + `total`
  (their sum = the book's realized return over that day's cross-section). `weights` is a static
  `Series` or a `DataFrame` history. Sum/cumsum over the window for the contribution of each bet.
- `brinson_attribution(port_weights, bench_weights, …)` — single-period **Brinson–Fachler** active
  return by GICS sector: **allocation** `(wₚ−w_b)(r_b,s−r_b)`, **selection** `w_b(rₚ,s−r_b,s)`, and
  **interaction**. Sectors come from the industry dummies; returns default to those reconstructed
  from the exposure history. The three columns sum to `rₚ − r_b` when both weight vectors sum to 1.

### 6. Calibration

`bias_statistic()` — standardize realized returns by predicted daily vol, `z_t = r_t / σ̂_t`. If the
forecast is honest, `z ~ N(0,1)`, so a rolling std near **1.0** means well-calibrated; **>1
under-forecasts** risk, <1 over-forecasts.

## Code map

| Concern | Location |
| --- | --- |
| Model: fit, covariance, decomposition, bias stat | `qhfi/barra/model.py` |
| Exposures: style factors, industry dummies, cap proxy | `qhfi/barra/exposures.py` |
| Cross-sectional transforms (winsorize, z-score) | `qhfi/factors/transforms.py` |
| Min-variance strategy consuming `Σ` | `qhfi/strategy/library/barra_minvar.py` |
| Tests | `tests/test_barra.py` |

### Key API (`BarraRiskModel`)

| Method | Returns |
| --- | --- |
| `fit(returns, exposures, industries, cap)` | fitted model |
| `from_panels(panels, universe, …)` | build exposures + fit in one call |
| `covariance(names)` | `Σ = X F Xᵀ + diag(Δ)` as an N×N frame |
| `risk_decomposition(weights)` | `{total_vol, factor_vol, specific_vol, pct_factor, factor_exposures}` |
| `risk_contributions(weights)` | per-position frame: `weight, mctr, cctr, pct` (Euler) |
| `factor_risk_contributions(weights)` | per-factor frame: `exposure, var_contribution, pct_total` |
| `factor_return_attribution(compound)` | cumulative realized factor returns (T×K) |
| `return_attribution(weights)` | per-date P&L: factor columns + `specific` + `total` |
| `brinson_attribution(port, bench, …)` | per-sector `allocation, selection, interaction, total` |
| `predict_vol(weights)` | annualized forecast vol (the `total_vol` field) |
| `factor_cov(annualize)` / `specific_var()` | `F` / `Δ` |

## Tests (`tests/test_barra.py`)

| Test | Asserts |
| --- | --- |
| `test_fit_recovers_known_factor_returns` | WLS recovers factor returns from synthetic data |
| `test_covariance_is_symmetric_psd` | `Σ` is symmetric and PSD |
| `test_risk_decomposition_adds_up` | `factor_vol² + specific_vol² = total_vol²`; `pct_factor ∈ [0,1]` |
| `test_bias_statistic_is_one_when_calibrated` | rolling std of `z` ≈ 1.0 when calibrated |
| `test_risk_contributions_sum_to_total_vol` | `Σ cctr = σ_p` and `Σ pct = 1` (Euler identity) |
| `test_factor_risk_contributions_match_factor_var` | per-factor contribs sum to factor variance / `pct_factor` |
| `test_factor_return_attribution_shapes` | cumulative == final running total; columns = factors |
| `test_return_attribution_identity` | per-date `total` == the book's realized return over the cross-section |
| `test_return_attribution_recovers_factor_pnl` | factor-only returns ⇒ `specific` ≈ 0, factors sum to `total` |
| `test_return_attribution_needs_history` | raises without `keep_exposure_history` |
| `test_brinson_attribution_adds_to_active_return` | allocation+selection+interaction sum to `rₚ−r_b` |
| `test_barra_minvar_weights_valid_and_backtests` | min-variance book is long-only, fully invested, backtestable |

Risk-gate tests live in `tests/test_gates.py` (gross/net/position breaches, frame last-row, drawdown
kill-switch, empty curve).

## What this gives you — and what it doesn't

**Provided**

- Factor *exposure* attribution (`Xᵀw` — what the portfolio is tilted toward).
- Factor-vs-specific *variance* decomposition (how much risk is systematic).
- **Per-position contribution to risk** (Euler allocation) — `risk_contributions(weights)` returns
  `mctr` = `(Σw)ᵢ/σ_p`, `cctr` = `wᵢ·mctrᵢ` (sums to `σ_p`), and the scale-free `pct` share. `Σw`
  is formed via `X F (Xᵀw) + Δ⊙w`, never the full N×N matrix.
- **Per-factor risk contribution** — `factor_risk_contributions(weights)` returns each factor's
  `exposure`, `var_contribution` = `feⱼ·(F·fe)ⱼ` (summing to factor variance), and `pct_total`.
- **Factor return attribution** — `factor_return_attribution(compound=False)` cumulates the stored
  per-date factor returns into what each factor *paid* over the fit window.
- **Portfolio-level return attribution** — `return_attribution(weights)` uses the stored exposure
  history to split a book's *realized* return day-by-day into factor-exposure × factor-return P&L
  plus a specific (selection) residual (`total` == the book's realized return each date).
- **Brinson sector attribution** — `brinson_attribution(port, bench)` decomposes single-period
  active return into per-sector allocation / selection / interaction (Brinson–Fachler).
- **Pre-trade risk gates** — `qhfi/risk/gates.py` `check_weights` (gross/net/per-position) and
  `check_drawdown` (kill-switch on equity-curve DD vs `max_drawdown_kill`) return auditable
  `GateDecision`s.
- A clean, invertible `Σ` for optimization (min-variance, MPT, Black-Litterman).

**Still open**

- **Multi-period Brinson linking** — `brinson_attribution` is single-period; chaining periods
  needs a smoothing algorithm (Menchero/Cariño) so the per-period active returns compound to the
  total without a residual.
- **Exposure-history memory at large N** — the history is `T·N·K` floats (fine for research
  universes, set `keep_exposure_history=False` for the full ~4.7k-name lake), and it inflates the
  pickled model artifact in the `ModelRepository`.

References: Rosenberg & Marathe (1976); Grinold & Kahn (2000) *Active Portfolio Management*; Menchero,
Orr & Wang (2011) *The Barra US Equity Model (USE4)*.
