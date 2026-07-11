# Models — Reference

> Part of the qhfi layer-reference trio: [Factors](factors.md) · [Strategies](strategies.md) · **Models**.
> The Barra risk model has its own deep dive in [RISK_ATTRIBUTION.md](RISK_ATTRIBUTION.md). See also [ARCHITECTURE.md](../ARCHITECTURE.md).

Packages: [`models/`](../src/qhfi/models), [`barra/`](../src/qhfi/barra), [`rates/`](../src/qhfi/rates), [`mdp/`](../src/qhfi/mdp), [`kalman/`](../src/qhfi/kalman), [`portfolio/`](../src/qhfi/portfolio), [`research/agents/`](../src/qhfi/research/agents). Research & paper only. Each model gets its own full entry.

> **Runnable-state caveats (read first).**
> 1. The **ML forecaster path** — [`models/predictive.py`](../src/qhfi/models/predictive.py) + [`models/features.py`](../src/qhfi/models/features.py) are Phase-1 stubs (`build_estimator(...) → None`, feature helpers → empty frames) and `qhfi/models/train.py` **does not exist** (imported by `model_strategy.py`, `rates/forecast.py`, `test_predictive_models.py`). Documented as **contract only**.
> 2. Several **model tests target a richer Phase-2 repository API** (taxonomy-partitioned paths, pydantic cards, tuple-returning `load`, `ModelStage.STAGING`, `ModelDomain.ALPHA`, `production()`) the **implemented flat store does not provide** — so `test_model_repository.py` (import), the repo-versioning test in `test_barra.py`/`test_mdp.py`/`test_rates_models.py`, and `test_predictive_models.py` (import) fail against current code, while the domain-algorithm tests pass.

---

## 1. Overview — two senses of "model"

1. **The artifact store** ([`models/`](../src/qhfi/models)) — an MLOps registry holding *no algorithms*; it versions, cards, and promotes any picklable fitted object.
2. **Domain algorithms** — the actual math (risk / allocation / curve / portfolio), each a self-contained `fit`-style class persisted *through* the store.

```
domain algorithm.fit(...) → picklable object ──save()──▶ ModelRepository ──promote()──▶ stage lifecycle
                                                          (card.json + model.pkl / version)
```

Every entry states: **Path**, **Signature/API**, **Math**, **Consumers**, **Tests**, **Notes**. Domain algorithms consume the [`core/types.py`](../src/qhfi/core/types.py) vocabulary (`Panel`, returns, `Universe`).

---

## 2. Artifact store

### 2.1 `ModelRepository` — [`models/repository.py`](../src/qhfi/models/repository.py)
- **Layout (flat):** `<root>/<name>/v<version>/{card.json, model.pkl}` (domain/asset-class live inside the card, not the path).
- **API:**
  ```python
  def __init__(self, root: str | Path) -> None
  def save(self, name, obj, *, framework=None, domain=None, asset_class=None, params=None,
           features=None, train_span=None, metrics=None, lineage=None, tags=None,
           stage=ModelStage.DRAFT) -> ModelCard          # auto version bump
  def load(self, name, version=None) -> Any              # unpickle (latest if None)
  def card(self, name, version) -> ModelCard
  def cards(self) -> list[ModelCard]                     # all, sorted (name, version)
  def latest(self, name) -> ModelCard
  def promote(self, name, version, stage) -> ModelCard
  ```
- **Guarantees:** atomic tmp-write+rename (`pickle.dumps` before any `mkdir`; `rmtree` on error); class-wide `RLock`; monotonic versions from 1; case-insensitive names (APFS); **PRODUCTION is singular** (`_archive_incumbents` demotes others); directory identity overrides card fields.
- **Consumers:** `rates/forecast.py`, the terminal "Trained Models" tab, all model builders. **Tests:** [`test_model_repository.py`](../tests/test_model_repository.py) ⚠ (targets the Phase-2 API — fails to import here).

### 2.2 `ModelCard` / `ModelStage` / `ModelDomain` — [`models/card.py`](../src/qhfi/models/card.py)
- **`ModelCard`** — a **plain dataclass** (not pydantic): `name, version, stage=DRAFT, framework, domain, asset_class, created_at, metrics, params, features, train_span, lineage, tags, path`. `to_dict()`/`from_dict()` (tolerant: bad stage → DRAFT, unknown domain → None). Imported from `qhfi.models.card` (`qhfi.models` exports only `ModelRepository`, `ModelStage`, `features`).
- **`ModelStage`:** `DRAFT, BACKTEST, PAPER, PRODUCTION, ARCHIVED` (strict — bad value raises).
- **`ModelDomain`:** `ALLOCATION, CURVE, RISK` (strict).
- **Notes:** no `ModelStage.STAGING` / `ModelDomain.ALPHA` (referenced by Phase-2 tests, absent here).

---

## 3. Domain algorithms

<a id="barra"></a>
### 3.1 `BarraRiskModel` — [`barra/model.py`](../src/qhfi/barra/model.py) · domain `RISK`
- **API:** `__init__(factor_halflife=252, specific_halflife=126, min_names=10, keep_exposure_history=True)`; `fit(returns, exposures, industries, cap)`; `covariance(names=None)`; plus `risk_decomposition`, `predict_vol`, `risk_contributions`, `factor_return_attribution`, `brinson_attribution`, module `bias_statistic(realized, predicted_vol, window=21)`.
- **Math:** per-date WLS cross-sectional regression of returns on `X` (5 style factors + industry dummies), weights ∝ √cap; `F` = EWMA factor covariance; `Δ` = EWMA specific variance; **`Σ = X F Xᵀ + diag(Δ)`** (daily; body: `sigma = x @ f @ x.T + np.diag(sv)`).
- **Consumers:** [`BarraMinVarStrategy`](strategies.md#barraminvar). **Tests:** [`test_barra.py`](../tests/test_barra.py) (math ✅; repo-versioning ⚠). **Notes:** deep dive → [RISK_ATTRIBUTION.md](RISK_ATTRIBUTION.md); exposures `X` built in [`barra/exposures.py`](../src/qhfi/barra/exposures.py) → [Factors §4](factors.md#barra-exposures). Persists under `RISK`.

### 3.2 `CurvePCA` — [`rates/pca.py`](../src/qhfi/rates/pca.py) · domain `CURVE`
- **API:** `__init__(n_components=3)`; `fit(curve)` (on `curve.diff()`); `loadings()` (tenor × {level, slope, curvature}); `transform(curve) = (curve − mean_level_) @ components_.T`; `explained()`.
- **Math:** PCA of daily curve **changes**; loadings sign-normalized to the economic convention (level +, slope ↑ with maturity, curvature belly-up).
- **Tests:** [`test_rates_models.py`](../tests/test_rates_models.py) (`explained()` index `[level,slope,curvature]`, Σ > 0.95).

### 3.3 `NelsonSiegel` — [`rates/nelson_siegel.py`](../src/qhfi/rates/nelson_siegel.py) · domain `CURVE`
- **API:** `__init__(lam=2.0)`; `fit(curve)`; `factors(curve)` (per-date OLS → `[level, slope, curvature]`); `fitted(betas)`; `rmse(curve)`; module `nelson_siegel_factors(curve, lam=2.0)`.
- **Math:**
  ```
  y(τ) = β0 + β1·L1(τ) + β2·L2(τ)
  L1(τ) = (1 − exp(−τ/λ)) / (τ/λ)     # slope loading
  L2(τ) = L1(τ) − exp(−τ/λ)          # curvature loading
  ```
- **Tests:** [`test_rates_models.py`](../tests/test_rates_models.py) (`rmse < 0.05`). **Notes:** parametric fixed-λ counterpart to `CurvePCA` — same three factors, different lens.

### 3.4 Curve container & analytics — [`rates/curve.py`](../src/qhfi/rates/curve.py)
Wide dates × tenor (par yields %). `curve_metrics(curve)` (level/slope/curvature), `carry_rolldown(curve, tenor, horizon_days=21)` (`carry = y·h`, `rolldown = (y − y_rolled)·τ`), `load_treasury_curve`, `tenor_years`, `order_tenors`.

### 3.5 Curve forecaster — [`rates/forecast.py`](../src/qhfi/rates/forecast.py) ⚠
- **API:** `curve_features(curve, target_tenor="10Y", horizon=21)`; `forward_change(curve, target="10Y", horizon=21)`; `train_curve_forecaster(curve, spec, *, target="10Y", horizon=21) -> (estimator, metrics, feature_names)`; `train_and_save_curve_forecaster(repo, name, curve, spec, ...)` (saves under `CURVE`/`RATES`).
- **Notes:** ⚠ depends on the stubbed `build_estimator` (→ `None`) → **not runnable here**; document the contract.

<a id="mdp"></a>
### 3.6 MDP core + solvers — [`mdp/core.py`](../src/qhfi/mdp/core.py) · domain `ALLOCATION`
- **API:** `@dataclass MDP(transition, reward, actions, gamma=0.95)` (validates shapes, `0 ≤ γ < 1`); `value_iteration(mdp, tol=1e-10, max_iter=10_000) -> (V, policy)`; `policy_iteration(mdp, max_iter=1_000)` (exact eval `solve(I − γP, r_π)`).
- **Math:** `V(s) = maxₐ [ R[s,a] + γ·Σ P[s,s'] V(s') ]`; `Q[s,a] = R[s,a] + γ·(P V)[s]`. Transition is **action-independent** (exogenous regime; action only sets exposure). Pure numpy, no qhfi imports.
- **Tests:** [`test_mdp.py`](../tests/test_mdp.py) (VI ≡ PI policies).

### 3.7 `RegimeModel` — [`mdp/regime.py`](../src/qhfi/mdp/regime.py) · domain `ALLOCATION`
- **API:** `RegimeModel(n_regimes=3, lookback=63, seed=0)`; `fit(market_returns)`; `label(market_returns)`. Module: `regime_features`, `transition_matrix(labels, n, smoothing=1.0)`, `regime_return_stats`.
- **Math:** two causal features — `vol = returns.rolling(63).std(ddof=0)·√252`, `drawdown = equity/equity.rolling(63).max() − 1`; `GaussianMixture(n, covariance_type="full")`; regimes **vol-ordered** (`argsort(means_[:,0])`) → 0 = calmest. Transition matrix Laplace-smoothed (+1). **Notes:** the discrete MDP state.

### 3.8 `RegimeAllocationMDP` — [`mdp/allocation.py`](../src/qhfi/mdp/allocation.py) · domain `ALLOCATION`
- **API:** `__init__(n_regimes=3, lookback=63, gamma=0.95, risk_aversion=3.0, rf_annual=0.0, action_grid=DEFAULT_ACTIONS, seed=0)` (`DEFAULT_ACTIONS = (0, .25, .5, .75, 1, 1.25, 1.5)`); `fit(market_returns, risky_returns)`; `optimal_fraction(regime)`; `policy_table()` (`ann_mean, ann_vol, value, risky_fraction`).
- **Math:** reward `R(s,a) = a·μ_s + (1−a)·r_f − ½·γ_risk·a²·σ²_s`; `fit` = regime → `P_` → `(mu_, var_)` → reward → `value_iteration` → `policy_ = action_grid[idx]`.
- **Consumers:** [`MDPStrategy`](strategies.md#mdpstrategy). **Tests:** [`test_mdp.py`](../tests/test_mdp.py) (calm ≥ storm risky fraction). Persists under `ALLOCATION`.

<a id="kalman"></a>
### 3.9 Kalman dynamic regression — [`kalman/filter.py`](../src/qhfi/kalman/filter.py)
- **API:** `kalman_regression(y, regressors: dict, delta=1e-4, obs_var=1e-3, prior_var=1.0)` → cols `alpha, beta_<name>..., spread, spread_var, z`; `kalman_hedge(y, x, ...)` (single-regressor; `beta_x → beta`).
- **Math:** online OLS with drifting coefficients `θ_t = θ_{t-1} + w_t`, `Vw = δ/(1−δ)·I` (smaller `δ` = stiffer); tracks filtered `θ`, forecast error `spread` (e_t), variance `spread_var` (Q_t), `z = e/√Q`.
- **Consumers:** [`KalmanPairsStrategy`](strategies.md#kalmanpairs), [`ButterflyStrategy`](strategies.md#butterfly). **Tests:** [`test_kalman_pairs.py`](../tests/test_kalman_pairs.py).

---

## 4. Portfolio stack — [`portfolio/`](../src/qhfi/portfolio)

### 4.1 Covariance — [`covariance.py`](../src/qhfi/portfolio/covariance.py)
`sample_cov(returns)`; `ledoit_wolf(returns) -> (sigma, shrinkage)` — Ledoit-Wolf (2004) shrinkage toward `F = (trace(S)/n)·I`. (No EWMA covariance here.) **Tests:** [`test_mpt_bl.py`](../tests/test_mpt_bl.py).

### 4.2 Black-Litterman — [`black_litterman.py`](../src/qhfi/portfolio/black_litterman.py)
`implied_returns(cov, w_market, risk_aversion=2.5) = δ·Σ·w`; `black_litterman(cov, pi, p, q, omega=None, tau=0.05)` = `μ_BL = [(τΣ)⁻¹ + PᵀΩ⁻¹P]⁻¹[(τΣ)⁻¹π + PᵀΩ⁻¹Q]`; `absolute_views(scores, confidence=1.0) -> (P=I, Q=scores, Ω=I/confidence)`. Default `Ω = diag(diag(P·τΣ·Pᵀ))`. **Tests:** [`test_mpt_bl.py`](../tests/test_mpt_bl.py).

### 4.3 Optimizers — [`optimize.py`](../src/qhfi/portfolio/optimize.py)
Closed-form with ridge solve `(Σ + ridge·I)⁻¹` (`ridge=1e-8`): `min_variance(cov)` = `w ∝ Σ⁻¹1` normalized; `mean_variance(mu, cov, risk_aversion=1.0)` = `(1/δ)·Σ⁻¹μ`; `max_sharpe(mu, cov, gross=1.0, dollar_neutral=False, long_only=False)` = tangency `Σ⁻¹μ`, optional demean/clip, scaled so `Σ|w| = gross`. **Tests:** [`test_mpt_bl.py`](../tests/test_mpt_bl.py) (`min_variance(diag([1,4])) = [0.8, 0.2]`).

### 4.4 Construction — [`construction.py`](../src/qhfi/portfolio/construction.py)
`PortfolioConstructor(ConstructionConfig).build(score, returns) -> TargetWeights`. Config: `gross=1.0, max_position=0.05, smoothing_halflife=10, target_vol=0.10, vol_lookback=60, max_leverage=3.0`. Pipeline: EWMA smooth → dollar-neutralize → scale to gross → hard per-name cap `clip(±max_position·gross)` (no renormalize — a concentration control) → causal vol-target with a 1-day lag. **Tests:** [`test_construction.py`](../tests/test_construction.py).

### 4.5 Sizing — [`sizing.py`](../src/qhfi/portfolio/sizing.py)
Weight → units, routed by `RiskBasis`. `NotionalSizing`: `weight·equity/(price·mult)`. `DV01Sizing(dv01_budget_per_equity=0.0005)`: `units = weight·equity·budget / (md·price·mult/10_000)`. `CompositeSizing`: `DV01Sizing` for `RiskBasis.DV01` (RATES/CREDIT), else `NotionalSizing` — what the backtest engine uses.

### 4.6 Factor-free allocators — [`allocations.py`](../src/qhfi/portfolio/allocations.py)
`equal_weight`, `inverse_vol`, `min_variance_long_only`, `max_sharpe_long_only` (all long-only, sum-to-1); collected in `ALLOCATORS`. **Tests:** [`test_allocations.py`](../tests/test_allocations.py).

---

<a id="predictive"></a>
## 5. ML forecaster — contract only (not runnable here)

The generic cross-sectional forward-return forecaster [`ModelStrategy`](strategies.md#modelstrategy) is built around. **Phase-1 stub in this checkout.**

- [`models/predictive.py`](../src/qhfi/models/predictive.py): `ModelSpec(StubBase)` (no kwarg validation), `build_estimator(*a, **k) -> None`.
- [`models/features.py`](../src/qhfi/models/features.py): PEP-562 `__getattr__` — any helper (`feature_panels`, `to_training_frame`, `to_feature_matrix`, `predictions_to_panel`) returns an empty `DataFrame`.
- `qhfi/models/train.py` **does not exist** (imported by `test_predictive_models.py`).
- **Intended contract:** `spec = ModelSpec(...)` → `estimator = build_estimator(spec)` → `X, y, idx = features.to_training_frame(panels, prices, horizon)` → `estimator.fit(X, y)` → `predictions_to_panel(...)`. Served mode needs a pre-fit estimator; walk-forward rebuilds per fold. Until implemented, both raise at runtime.

---

## 6. LLM "models" — [`research/agents/`](../src/qhfi/research/agents)

Each takes an `LLMClient` ([`research/client.py`](../src/qhfi/research/client.py); `LangGraphBridge`/`CrewAIBridge` are `NotImplementedError`).

### 6.1 `IdeationAgent` — [`ideation.py`](../src/qhfi/research/agents/ideation.py)
`ideate(theme, n=5) -> list[Hypothesis]` (fields: `title, rationale, signal_description, universe_hint, expected_edge`). ⚠ `NotImplementedError`.

### 6.2 `CodegenAgent` — [`codegen.py`](../src/qhfi/research/agents/codegen.py)
`draft(hypothesis) -> str`; `materialize(source)` (sandbox-load). ⚠ `NotImplementedError` (sandbox loader also raises). `momentum` strategy is its worked template.

### 6.3 `CriticAgent` — [`critic.py`](../src/qhfi/research/agents/critic.py)
`review(scorecard) -> CriticVerdict(block, concerns, suggested_tests)` — can only *block*, never approve. ⚠ `NotImplementedError`.

### 6.4 `SectorResearchAgent` — [`sector.py`](../src/qhfi/research/agents/sector.py) ✅ implemented
LLM equity analyst scoped to one GICS sector, grounded in factor evidence. `_FACTORS = {momentum, lowvol, reversal}`; `evidence(sector, prices, universe)` is deterministic quant (composite z-rank + within-sector IC via `factors.evaluation`); `research(...)` calls `client.structured(...)` and merges scores back. Context from [`sector_context.py`](../src/qhfi/research/agents/sector_context.py) (`SECTOR_PROFILES` for 11 sectors, `augment_system`). **Tests:** [`test_sector_research.py`](../tests/test_sector_research.py).

---

## 7. Usage, math, design & gaps

```python
# version + promote a model
from qhfi.models.repository import ModelRepository
from qhfi.models.card import ModelStage, ModelDomain
repo = ModelRepository("./models")
card = repo.save("regime-allocator", fitted_mdp, domain=ModelDomain.ALLOCATION,
                 metrics={"sharpe_oos": 0.9}, stage=ModelStage.BACKTEST)
model = repo.load("regime-allocator")                 # latest, unpickled
repo.promote("regime-allocator", card.version, ModelStage.PAPER)

# fit domain algorithms (runnable — tests/test_mdp.py, test_mpt_bl.py, test_rates_models.py)
RegimeAllocationMDP(n_regimes=2, risk_aversion=3.0).fit(mkt, risky).policy_table()  # calm ≥ storm fraction
min_variance(np.diag([1.0, 4.0]))                     # → [0.8, 0.2]
CurvePCA(3).fit(curve).explained()                    # [level, slope, curvature], Σ > 0.95
```

**Scripts:** `scripts/build_barra_model.py`, `build_mdp_allocator.py`, `build_portfolio.py`, `build_ml_model.py` (⚠ needs predictive impl).

- **Math:** Barra `Σ = XFXᵀ + diag(Δ)`; MDP Bellman over regimes with mean-variance reward; PCA vs Nelson-Siegel (empirical vs parametric level/slope/curvature); Black-Litterman posterior = prior `π = δΣw` blended with views; ridge everywhere in the optimizers; DV01 vs notional risk-basis sizing.
- **Design:** store/algorithm split; duck-typed models (no base class — picklable + `ModelDomain` tag is enough); flat-layout single-object `load` (the taxonomy-partitioned, pydantic-card API in some tests is Phase-2); contract-first ML seam; LLM proposes / framework disposes.
- **Gaps / stubs / not-runnable:** ML forecaster (`predictive`/`features` stubs + missing `train.py`); repository API drift (the repo-versioning tests in `test_model_repository.py`/`test_barra.py`/`test_mdp.py`/`test_rates_models.py` target a Phase-2 API — those tests fail, domain-algorithm tests pass); `IdeationAgent`/`CodegenAgent`/`CriticAgent` + bridges + sandbox loader `NotImplementedError` (only `SectorResearchAgent` implemented).

**Tests** (verified in the project venv). ✅ pass — [`test_mpt_bl.py`](../tests/test_mpt_bl.py), [`test_allocations.py`](../tests/test_allocations.py), [`test_construction.py`](../tests/test_construction.py), [`test_kalman_pairs.py`](../tests/test_kalman_pairs.py), [`test_sector_research.py`](../tests/test_sector_research.py), and the algorithm tests of [`test_barra.py`](../tests/test_barra.py), [`test_mdp.py`](../tests/test_mdp.py), [`test_rates_models.py`](../tests/test_rates_models.py). ⚠ fail against current code — the repository-versioning test in each of `test_barra.py`/`test_mdp.py`/`test_rates_models.py`, the curve-forecaster-save in `test_rates_models.py`, and [`test_model_repository.py`](../tests/test_model_repository.py) + [`test_predictive_models.py`](../tests/test_predictive_models.py) (import failures).
