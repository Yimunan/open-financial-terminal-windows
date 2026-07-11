# Factors — Reference

> Part of the qhfi layer-reference trio: **Factors** · [Strategies](strategies.md) · [Models](models.md).
> See also [ARCHITECTURE.md](../ARCHITECTURE.md) (§6 factor-research layer), [RISK_ATTRIBUTION.md](RISK_ATTRIBUTION.md) (Barra risk model), and the worked case study [notebooks/new_alpha_study_2026-06.md](../notebooks/new_alpha_study_2026-06.md).

Package: [`src/qhfi/factors/`](../src/qhfi/factors) (+ [`barra/exposures.py`](../src/qhfi/barra/exposures.py)). Research & paper only. Each factor below gets its own full entry; the shared contract and tooling bracket the catalog.

> **Status note:** the repo `README.md` still calls qhfi a "scaffold / NotImplementedError stub." That is stale — the factors below are implemented and tested. The only deliberate factor stubs are `CarryFactor.compute` and `transforms.beta_neutralize`.

---

## 1. The `Factor` contract — [`factors/base.py`](../src/qhfi/factors/base.py)

A factor is a pure, look-ahead-free function from a price/feature `Panel` to a raw score `Panel`.

```python
class FactorParams(BaseModel): ...                     # pydantic; typed per-factor hyperparameters

class Factor(ABC):
    name: str = ""                                     # registry key
    direction: int = 1                                 # +1 higher=long, -1 lower=long
    params_model: type[FactorParams] = FactorParams
    def __init__(self, params: FactorParams | None = None) -> None: ...
    @abstractmethod
    def compute(self, prices: Panel, universe: Universe) -> Panel: ...   # raw scores, no look-ahead
    def signed(self, prices, universe) -> Panel:  return self.compute(...) * self.direction
```

**Invariant:** a score at date *t* uses only data through *t* (evaluation aligns it against *forward* returns, so leakage inflates IC). **`direction`** normalizes sign so `signed()` always means "more long." **Data contracts** ([`core/types.py`](../src/qhfi/core/types.py)): input/output are wide `Panel`s (dates × `instrument_id`); multi-field alphas carry a `MarketPanels`.

Every catalog entry below states: **Class · path**, **Params (defaults)**, **Formula**, **Does**, **Direction**, **Consumers**, **Tests**, **Notes**.

---

## 2. Library factors — [`factors/library.py`](../src/qhfi/factors/library.py)

<a id="fac-momentum"></a>
### 2.1 `momentum` — `MomentumFactor`
- **Path:** [`factors/library.py`](../src/qhfi/factors/library.py) · registered · `direction = +1`
- **Params:** `lookback=90, gap=5` (`MomentumParams`)
- **Formula:** `prices.shift(gap) / prices.shift(gap + lookback) - 1.0`
- **Does:** Trailing total return over `lookback` days, skipping the most recent `gap` days to sidestep short-term reversal. Higher = stronger trend = long.
- **Consumers:** `FactorStrategy`, `ModelStrategy`, `SectorResearchAgent`, `barra/exposures.py` (style momentum).
- **Tests:** [`test_factors.py`](../tests/test_factors.py). **Notes:** pure shifts → look-ahead-free.

<a id="fac-volatility"></a>
### 2.2 `volatility` — `VolatilityFactor`
- **Path:** [`factors/library.py`](../src/qhfi/factors/library.py) · registered · `direction = −1`
- **Params:** `window=60` (`VolatilityParams`)
- **Formula:** `prices.pct_change().rolling(window).std()`
- **Does:** Trailing realized volatility of daily returns. `direction = −1` encodes the **low-volatility anomaly** (lower vol → more long).
- **Consumers:** `FactorStrategy`, `ModelStrategy`, `SectorResearchAgent` (`lowvol`), enables the `low_volatility` strategy (planned).
- **Tests:** [`test_factors.py`](../tests/test_factors.py).

<a id="fac-reversal"></a>
### 2.3 `reversal` — `ShortTermReversalFactor`
- **Path:** [`factors/library.py`](../src/qhfi/factors/library.py) · registered · `direction = −1`
- **Params:** `window=5` (`ReversalParams`)
- **Formula:** `prices.pct_change(window)`
- **Does:** Recent short-window return; `direction = −1` bets on mean reversion (recent losers bounce). Complements momentum at a faster horizon.
- **Consumers:** `FactorStrategy`, `ModelStrategy`, `SectorResearchAgent`. **Tests:** [`test_factors.py`](../tests/test_factors.py).

<a id="fac-value"></a>
### 2.4 `value` — `ValueFactor(FundamentalFactor)`
- **Path:** [`factors/library.py`](../src/qhfi/factors/library.py) · registered · `direction = +1`
- **Params:** none — carries a point-in-time metric panel.
- **Formula:** `metric_panel.reindex(index=prices.index, columns=prices.columns).ffill()`
- **Does:** Cheapness from a fundamental ratio (`earnings_yield` E/P, `book_yield` B/P). Higher yield = cheaper = long.
- **Construction:** `ValueFactor(store.panel(universe.instruments, "earnings_yield"))` (carries data → not registry-instantiable).
- **Notes:** PIT guard — the sparse report-date panel is reindexed onto the daily grid and forward-filled, so a value at *t* is the latest *publicly known* figure, never a future restatement. **Tests:** [`test_factors.py`](../tests/test_factors.py).

<a id="fac-quality"></a>
### 2.5 `quality` — `QualityFactor(FundamentalFactor)`
- **Path:** [`factors/library.py`](../src/qhfi/factors/library.py) · registered · `direction = +1`
- **Params:** none — carries a metric panel (ROE, gross margin, low leverage).
- **Formula:** same PIT reindex+ffill as `value`.
- **Does:** Profitability/soundness. Higher = better = long. **Construction:** `QualityFactor(store.panel(universe.instruments, "roe"))`.
- **Notes:** same PIT look-ahead guard as `value`. **Tests:** [`test_factors.py`](../tests/test_factors.py).

<a id="fac-carry"></a>
### 2.6 `carry` — `CarryFactor` ⚠ stub
- **Path:** [`factors/library.py`](../src/qhfi/factors/library.py) · registered · `direction = +1`
- **Formula:** `raise NotImplementedError("TODO: needs carry/funding/roll-yield panel per asset class")`
- **Does (intended):** Yield earned for holding — funding (crypto perps), roll (futures), dividend (equities).
- **Notes:** deliberate stub; blocks the `carry` strategy (planned). Needs a carry/funding data panel.

**`FundamentalFactor`** (base for `value`/`quality`) — `__init__(self, metric_panel, params=None)`; `compute` = reindex-onto-daily-grid + `ffill` (the PIT guard). Built explicitly because it carries data.

---

## 3. Alpha101 formulaic alphas — [`factors/alpha101.py`](../src/qhfi/factors/alpha101.py)

WorldQuant-style price/volume alphas (Kakushadze 2015). Each subclasses `Alpha` (carries a `MarketPanels`), sets `direction = +1` (the formula encodes its own sign), and implements `expr(self, m) -> Panel`. `op` = [`factors/operators.py`](../src/qhfi/factors/operators.py). Registered into `ALL_ALPHAS` (a list, not the string registry — alphas carry data). **Tests:** [`test_alpha101.py`](../tests/test_alpha101.py).

<a id="fac-alpha101"></a>
### 3.1 `alpha101` — `Alpha101`
- **Formula:** `(m.close - m.open) / ((m.high - m.low) + 0.001)`
- **Does:** Intraday momentum — the open→close move normalized by the day's range.

<a id="fac-alpha006"></a>
### 3.2 `alpha006` — `Alpha006`
- **Formula:** `-1 * op.correlation(m.open, m.volume, 10)`
- **Does:** Price/volume divergence — negative 10-day rolling correlation of open and volume.

<a id="fac-alpha012"></a>
### 3.3 `alpha012` — `Alpha012`
- **Formula:** `np.sign(op.delta(m.volume, 1)) * (-1 * op.delta(m.close, 1))`
- **Does:** Volume-confirmed reversal — fade the last close move, gated by the sign of the volume change.

<a id="fac-alpha041"></a>
### 3.4 `alpha041` — `Alpha041`
- **Formula:** `(m.high * m.low) ** 0.5 - m.vwap`
- **Does:** Geometric mid vs VWAP (VWAP ≈ typical price `(H+L+C)/3` — no true intraday VWAP is stored).

<a id="fac-alpha054"></a>
### 3.5 `alpha054` — `Alpha054`
- **Formula:** `(-1 * (m.low - m.close) * m.open ** 5) / ((m.low - m.high) * m.close ** 5)`
- **Does:** Where the close sits within the day's range, power-weighted by open/close.

<a id="fac-alpha004"></a>
### 3.6 `alpha004` — `Alpha004`
- **Formula:** `-1 * op.ts_rank(op.rank(m.low), 9)`
- **Does:** Short-term low-price reversal — time-series rank of the cross-sectional rank of the low.

<a id="fac-alpha013"></a>
### 3.7 `alpha013` — `Alpha013`
- **Formula:** `-1 * op.rank(op.covariance(op.rank(m.close), op.rank(m.volume), 5))`
- **Does:** Negative rank of the covariance of ranked close and ranked volume (price/volume co-movement).

<a id="fac-alpha033"></a>
### 3.8 `alpha033` — `Alpha033`
- **Formula:** `op.rank(-1 * (1 - (m.open / m.close)))`  (i.e. `rank(open/close - 1)`)
- **Does:** Rank of the intraday open-to-close ratio.

**Operators** ([`factors/operators.py`](../src/qhfi/factors/operators.py)) — the Alpha101 vocabulary on wide panels: cross-sectional `rank`(pct), `scale`, `signedpower`; element-wise `delay`, `delta`; rolling `ts_sum/mean/std/min/max/argmax/argmin/ts_rank/product/decay_linear`; pairwise `correlation`, `covariance`. `MarketPanels` ([`factors/market.py`](../src/qhfi/factors/market.py)) bundles `open/high/low/close/volume` with derived `vwap = (H+L+C)/3`, `returns`, `adv(d)`.

---

<a id="barra-exposures"></a>
## 4. Barra risk-factor exposures — [`barra/exposures.py`](../src/qhfi/barra/exposures.py)

The design matrix `X` of the cross-sectional risk model (`STYLE_FACTORS = ("size","beta","momentum","resid_vol","reversal")`). Each is causal; each raw column is `_standardize`d (`zscore(winsorize(·, 0.02))`). Dollar ADV `(close·volume)` is the cap/size proxy and the WLS weight (no `market_cap` in the lake). **Tests:** [`test_barra.py`](../tests/test_barra.py). The covariance model that consumes `X` is in [`barra/model.py`](../src/qhfi/barra/model.py) → [Models#barra](models.md#barra) / [RISK_ATTRIBUTION.md](RISK_ATTRIBUTION.md).

<a id="fac-barra-size"></a>
### 4.1 `size` — **Raw:** `np.log(cap_proxy(panels, adv_window))` (log dollar ADV). Market-cap stand-in.
<a id="fac-barra-beta"></a>
### 4.2 `beta` — **Raw:** rolling CAPM beta vs equal-weight market (`cov/var_m`, `beta_window=252`). Market sensitivity.
<a id="fac-barra-momentum"></a>
### 4.3 `momentum` — **Raw:** `close.shift(mom_gap) / close.shift(mom_gap + mom_lookback) - 1` (12-1 return, `mom_gap=21, mom_lookback=252`).
<a id="fac-barra-residvol"></a>
### 4.4 `resid_vol` — **Raw:** `resid.rolling(vol_window).std(ddof=0)` (`vol_window=63`). Idiosyncratic (market-residual) volatility.
<a id="fac-barra-reversal"></a>
### 4.5 `reversal` — **Raw:** `close.pct_change(rev_window)` (`rev_window=21`). Trailing one-month return.
<a id="fac-barra-industry"></a>
### 4.6 `industry_dummies` — one 0/1 column per GICS sector (`instrument × industry`). Styles + industry dummies span the market intercept, so no separate market column is needed.

Key signatures: `style_exposures(panels, market=None, beta_window=252, vol_window=63, mom_lookback=252, mom_gap=21, rev_window=21, adv_window=20, winsor=0.02) -> dict[str, Panel]`; `industry_dummies(universe, level="gics_sector") -> pd.DataFrame`; `cap_proxy(panels, window=20) = panels.adv(window)`.

---

## 5. Supporting subsystems (the pipeline around factors)

### 5.1 Transforms (hygiene) — [`factors/transforms.py`](../src/qhfi/factors/transforms.py)
All row-wise (per-date), pure, NaN-tolerant. `winsorize(panel, lower=0.01, upper=0.99)` (clip to cross-sectional quantiles) · `zscore(panel)` (`(x−mean)/std`, ddof=0) · `rank(panel, normalize=True)` (→ ≈`[-0.5, 0.5]`) · `neutralize(panel, groups)` (subtract per-sector mean) · `combine(factors, weights=None)` (blend; equal-weight default; `ValueError` if empty) · `beta_neutralize(...)` ⚠ `NotImplementedError`. Canonical order: **winsorize → zscore → neutralize → combine**.

### 5.2 Evaluation (grading gate) — [`factors/evaluation.py`](../src/qhfi/factors/evaluation.py)
`forward_returns(prices, horizon=1) = prices.shift(-horizon)/prices − 1` · `information_coefficient(factor, prices, horizon=1, method="spearman")` (daily cross-sectional corr of score vs forward return) · `ic_summary(ic) -> ICSummary(mean_ic, ic_std, ic_ir=mean/std, t_stat=ic_ir·√n, hit_rate, n)` · `quantile_returns(..., q=5)` + `spread` (top−bottom) · `ic_decay(..., horizons=(1,2,3,5,10,21))` · `autocorrelation(factor, lag=1)` (turnover proxy). **Tests:** [`test_factors.py`](../tests/test_factors.py).

### 5.3 Selection — [`factors/selection.py`](../src/qhfi/factors/selection.py)
`vif_prune(signals, threshold=5.0)` (iterative variance-inflation elimination) · `ic_weights(signals, prices, horizon=5)` (weight ∝ IC-IR, `Σ|w|=1`; in-sample → use per-fold). **Tests:** [`test_selection.py`](../tests/test_selection.py).

### 5.4 Heatmap — [`factors/heatmap.py`](../src/qhfi/factors/heatmap.py)
Diagnostic builders (`factor_correlation`, `ic_over_time`, `ic_scorecard`, `ic_decay_matrix`, `asset_correlation`) + `render_heatmap(df, title, *, center=0.0, per_column=False, ...)`. `SCORECARD_METRICS = ["mean_ic","ic_ir","t_stat","hit_rate","Q_spread"]`. **Tests:** [`test_factor_heatmap.py`](../tests/test_factor_heatmap.py).

### 5.5 Registry — [`factors/registry.py`](../src/qhfi/factors/registry.py)
`register(cls)` (key = `cls.name` or class name; `ValueError` on dup) · `get(name)` · `all_names()`. Fires when `qhfi.factors.library` is imported. Alpha101 uses `ALL_ALPHAS` instead (data-carrying).

---

## 6. Usage & examples

```python
# compute + grade one factor (tests/test_factors.py)
import qhfi.factors.evaluation as fe
from qhfi.factors.library import MomentumFactor
mom  = MomentumFactor(MomentumFactor.params_model(lookback=20, gap=1)).compute(prices, universe)
summ = fe.ic_summary(fe.information_coefficient(mom, prices, horizon=5))   # ICSummary(...)

# blend several factors (hygiene → combine)
import qhfi.factors.transforms as tf
std   = {n: tf.zscore(tf.winsorize(f.signed(prices, universe))) for n, f in factors.items()}
alpha = tf.combine(std)                       # equal-weight blend → one score panel

# build the Alpha101 set
from qhfi.factors.market import MarketPanels
from qhfi.factors.alpha101 import ALL_ALPHAS
m = MarketPanels.from_store(store, universe)
signals = {cls(m).name: cls(m).compute(m.close, universe) for cls in ALL_ALPHAS}
```

**Scripts:** `scripts/build_multialpha.py`, `eval_alpha101.py`, `eval_value_factor.py`, `factor_heatmap.py`. **Case study:** [notebooks/new_alpha_study_2026-06.md](../notebooks/new_alpha_study_2026-06.md). Blends feed `FactorStrategy` — see [Strategies](strategies.md#factorstrategy).

---

## 7. Math, design & gaps

- **Look-ahead-free scores** (trailing windows / shifts; fundamentals reindex+ffill on the daily grid). Evaluation shifts returns *forward*, so leakage is caught as inflated IC.
- **IC / IC-IR** — per-date cross-sectional (rank) correlation vs forward returns; `IC-IR = mean/std(IC)`, `t = IC-IR·√n`. Promotion needs positive, stable IC-IR + monotone quantile spread + acceptable decay/turnover; multi-alpha studies deflate Sharpe ([`evaluation/deflated_sharpe.py`](../src/qhfi/evaluation/deflated_sharpe.py)).
- **Design:** factor = pure function of data (testable, LLM-codegen-safe); `direction` keeps `compute` literal; registry for price-only factors, constructor-carried data for fundamentals/alphas; grade before backtest.
- **Gaps / stubs:** `CarryFactor.compute`, `transforms.beta_neutralize` (both `NotImplementedError`); `ic_weights` is in-sample. `value`/`quality` exist but their dedicated strategies are PLANNED (see [Strategies](strategies.md#taxonomy)).
- **Documented elsewhere:** Barra covariance/attribution → [RISK_ATTRIBUTION.md](RISK_ATTRIBUTION.md); factor flow → [ARCHITECTURE.md §6](../ARCHITECTURE.md).
