# Strategies — Reference

> Part of the qhfi layer-reference trio: [Factors](factors.md) · **Strategies** · [Models](models.md).
> Complements (does not duplicate) [MARKET_MAKING.md](MARKET_MAKING.md) (quoting math) and [event_driven_engine.md](event_driven_engine.md) (streaming backtester). See also [ARCHITECTURE.md §8](../ARCHITECTURE.md).

Packages: [`src/qhfi/strategy/`](../src/qhfi/strategy), [`backtest/`](../src/qhfi/backtest), [`trading/`](../src/qhfi/trading), [`execution/`](../src/qhfi/execution). Research & paper only. Each strategy below gets its own full entry.

---

## 1. The `Strategy` contract — [`strategy/base.py`](../src/qhfi/strategy/base.py)

```python
class StrategyParams(BaseModel): ...                   # pydantic; typed per-strategy hyperparameters
class Strategy(ABC):
    name: str = ""; params_model: type[StrategyParams] = StrategyParams
    def __init__(self, params=None) -> None: ...
    @abstractmethod
    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights: ...
```

**Invariant:** *"weights for date t may only use information available at the close of t (they are applied to the t+1 return inside the engine)."* Three execution shapes fan out from this:

| Shape | Base class | Method | Output |
|---|---|---|---|
| Vectorized | `Strategy` | `generate_weights(prices, universe)` | `TargetWeights` |
| Event-driven | `EventStrategy` | `on_market(event, book)` | `SignalEvent[]` |
| Market-making | `QuotingStrategy` | `on_book(event, book)` | `QuoteEvent[]` |

`WeightStrategyAdapter` replays any vectorized strategy onto the event engine unchanged; `BookView` (`equity/position/last_price`) is the read-only Protocol streaming strategies see. **Data contracts** ([`core/types.py`](../src/qhfi/core/types.py)): `Panel` in, `TargetWeights` (dates × id) out — no I/O; sizing/orders happen downstream.

Every catalog entry states: **Path · registration**, **Params (defaults)**, **Mechanism**, **Style/exposure**, **Uses**, **Tests**, **Notes**.

---

<a id="taxonomy"></a>
## 2. The strategy taxonomy — [`strategy/taxonomy.py`](../src/qhfi/strategy/taxonomy.py)

A frozen classification of strategy *kinds* — source of truth for the space, wider than what is built. **Seven dimensions:** style (12), horizon (4), exposure (5), signal axis (2), **data input** (6: price/fundamentals/reference/carry/order_book/alternative — last two declared-but-empty), status (live/stub/planned), asset classes. Views: `get`, `by_style`, `by_asset_class`, `by_status`, `by_data_input`, `describe`. Enforced by [`test_strategy_taxonomy.py`](../tests/test_strategy_taxonomy.py). The 13 kinds:

| name | style | horizon | exposure | axis | status | data_input |
|---|---|---|---|---|---|---|
| `factor` | multi_factor | swing | dollar_neutral | X-sec | 🟢 LIVE | price |
| `model` | statistical | swing | dollar_neutral | X-sec | 🟢 LIVE | price |
| `mdp` | macro | swing | long_only | time-series | 🟢 LIVE | price |
| `kalman_pairs` | stat_arb | swing | dollar_neutral | time-series | 🟢 LIVE | price |
| `butterfly` | stat_arb | swing | dollar_neutral | time-series | 🟢 LIVE | price |
| `barra_minvar` | risk_based | monthly | long_only | X-sec | 🟢 LIVE | reference |
| `momentum` | momentum | swing | dollar_neutral | X-sec | 🟡 STUB | price |
| `value` | value | monthly | dollar_neutral | X-sec | ⚪ PLANNED | fundamentals |
| `quality` | quality | monthly | dollar_neutral | X-sec | ⚪ PLANNED | fundamentals |
| `low_volatility` | low_volatility | swing | dollar_neutral | X-sec | ⚪ PLANNED | price |
| `reversal` | reversal | swing | dollar_neutral | X-sec | ⚪ PLANNED | price |
| `carry` | carry | swing | long_short | X-sec | ⚪ PLANNED | carry |

The market-making quoters and infra are *not* in this taxonomy (it covers vectorized alpha kinds only).

---

<a id="catalog"></a>
## 3. Vectorized library — [`strategy/library/`](../src/qhfi/strategy/library)

<a id="factorstrategy"></a>
### 3.1 `factor` — `FactorStrategy`
- **Path:** [`factor_strategy.py`](../src/qhfi/strategy/library/factor_strategy.py) · **not** string-registered (carries factors) · style multi_factor, dollar-neutral, swing.
- **Params:** `quantile=0.2, gross=1.0, long_only=False, winsor=0.02` (`FactorStrategyParams`).
- **Constructor:** `FactorStrategy(factors: list[Factor], blend=None, sectors=None, params=None)`.
- **Mechanism:** `composite_score` per factor = `f.signed()` → `winsorize(·, winsor, 1−winsor)` → `zscore` → optional `neutralize(·, sectors)` → `transforms.combine(·, blend)`. Then `long_short_weights(score, quantile, gross, long_only)`.
- **`long_short_weights`** (module fn): per date, `k = max(1, round(n·quantile))`; long top `k`, short bottom `k` (disjoint, `k = min(k, n//2)`); weights `±(gross/2)/k` (or `gross/len(longs)` if `long_only`).
- **Uses:** [Factors](factors.md). **Tests:** [`test_validation.py`](../tests/test_validation.py). **Notes:** most cross-sectional strategies are an instance of this.

<a id="modelstrategy"></a>
### 3.2 `model` — `ModelStrategy`
- **Path:** [`model_strategy.py`](../src/qhfi/strategy/library/model_strategy.py) · `@register` as `"model"` · style statistical, dollar-neutral, swing.
- **Params:** `quantile=0.2, gross=1.0, long_only=False, winsor=0.02, horizon=5, refit=False, embargo=0`.
- **Constructor:** `ModelStrategy(factors, estimator=None, spec=None, sectors=None, params=None)` (`refit and spec is None → ValueError`; `not refit and estimator is None → ValueError`).
- **Mechanism:** standardized factors → `X/y` via `qhfi.models.features` → predicted forward returns → `long_short_weights`. **Served** (`refit=False`): pre-fit `estimator`. **Walk-forward** (`refit=True`): `train_prices = prices.iloc[:len−embargo]`, rebuild from `spec`, `build_estimator(spec).fit(X, y)` per call.
- **Uses:** [Models#predictive](models.md#predictive). ⚠ **Notes:** the ML path (`qhfi.models.predictive`/`features`/`train`) is a Phase-1 stub / absent in this checkout → **not runnable here**. Tests: [`test_predictive_models.py`](../tests/test_predictive_models.py) (fails to import).

<a id="mdpstrategy"></a>
### 3.3 `mdp` — `MDPStrategy`
- **Path:** [`mdp_strategy.py`](../src/qhfi/strategy/library/mdp_strategy.py) · `@register` as `"mdp"` · style macro, **long-only**, swing, time-series.
- **Params:** `n_regimes=3, lookback=63, gamma=0.95, risk_aversion=3.0, max_leverage=1.5, action_step=0.25, base="equal"` (`"equal"|"inverse_vol"`).
- **Mechanism:** build a long-only risky book (`_base_weights`: equal, or inverse-vol via `ledoit_wolf`), collapse to `book_returns`, fit a `RegimeAllocationMDP(action_grid = arange(0, max_leverage, action_step))` on the book, then `weights = np.outer(fraction, base)` where `fraction = labels.map(optimal_fraction)`. Remainder implicitly cash.
- **Uses:** [Models#mdp](models.md#mdp). **Tests:** [`test_mdp.py`](../tests/test_mdp.py). **Notes:** a regime risk-throttle, not a cross-sectional alpha.

<a id="kalmanpairs"></a>
### 3.4 `kalman_pairs` — `KalmanPairsStrategy`
- **Path:** [`kalman_pairs.py`](../src/qhfi/strategy/library/kalman_pairs.py) · constructed with two ids (not registered) · style stat_arb, dollar-neutral, time-series.
- **Params:** `delta=1e-4, obs_var=1e-3, entry_z=1.0, exit_z=0.0, gross=1.0, warmup=20`.
- **Constructor:** `KalmanPairsStrategy(y_id, x_id, params=None)` (`y_id == x_id → ValueError`).
- **Mechanism:** `kalman_hedge(y, x, delta, obs_var)` tracks a time-varying hedge ratio `β_t`; trade the filtered spread's z-score with **hysteresis** — enter long-spread when `z < −entry_z`, short-spread when `z > +entry_z`, exit through `exit_z` (`hysteresis_positions`). Legs `1 : −β_t`, re-hedged daily, `scale_to_gross`.
- **Uses:** [Models#kalman](models.md#kalman). **Tests:** [`test_kalman_pairs.py`](../tests/test_kalman_pairs.py).

<a id="butterfly"></a>
### 3.5 `butterfly` — `ButterflyStrategy`
- **Path:** [`butterfly.py`](../src/qhfi/strategy/library/butterfly.py) · constructed with belly + 2 wings (not registered) · style stat_arb, dollar-neutral.
- **Params:** `weighting="kalman"` (`"kalman"|"fixed"`)`, delta=1e-4, obs_var=1e-3, z_window=60, entry_z=1.0, exit_z=0.0, gross=1.0, warmup=20`.
- **Constructor:** `ButterflyStrategy(belly_id, wing_ids: tuple[str,str], params=None)` (needs 3 distinct legs).
- **Mechanism:** **kalman** — `kalman_regression(belly, {w1, w2})` residual spread, rolling-window z, legs `1 : −b1 : −b2`. **fixed** — structural spread `w1 − 2·belly + w2` (units `1:−2:1`), rolling z. Long belly when cheap vs wings; `scale_to_gross`.
- **Uses:** [Models#kalman](models.md#kalman). **Tests:** [`test_butterfly.py`](../tests/test_butterfly.py). **Notes:** the 3-leg generalization of `kalman_pairs`.

<a id="barraminvar"></a>
### 3.6 `barra_minvar` — `BarraMinVarStrategy`
- **Path:** [`barra_minvar.py`](../src/qhfi/strategy/library/barra_minvar.py) · carries a `MarketPanels` (not registered) · style risk_based, **long-only**, monthly · `data_input = reference`.
- **Params:** `estimation_window=504, rebalance_days=21, factor_halflife=252, specific_halflife=126, gross=1.0`.
- **Mechanism:** each ~month fit `BarraRiskModel(...).fit(returns[sl], style_exposures, industry_dummies, cap[sl])`, form `Σ = X F Xᵀ + diag(Δ)`, hold `w ∝ Σ⁻¹1` via `_min_var_long_only` (clip negatives, renormalize; `1/N` fallback), carried between rebalances. `except (ValueError, LinAlgError): pass` keeps prior weights.
- **Uses:** [Models#barra](models.md#barra), [RISK_ATTRIBUTION.md](RISK_ATTRIBUTION.md). **Tests:** [`test_barra.py`](../tests/test_barra.py).

<a id="momentum-stub"></a>
### 3.7 `momentum` — `Momentum` ⚠ stub
- **Path:** [`momentum.py`](../src/qhfi/strategy/library/momentum.py) · `@register` as `"momentum"` (STUB).
- **Params:** `lookback=90, gap=5, quantile=0.2, gross=1.0, long_only=False`.
- **Mechanism:** `generate_weights` raises `NotImplementedError("TODO: implement ranked cross-sectional momentum on the close panel")`. Intended signal (documented in-body): `prices.shift(gap)/prices.shift(gap+lookback) − 1`.
- **Notes:** registered but not runnable — the codegen agent's worked template.

**Planned kinds** (`value`, `quality`, `low_volatility`, `reversal`, `carry`) — the enabling factor exists ([Factors](factors.md)) but no strategy class yet; each is an intended thin `FactorStrategy` wrapper.

**Shared spread mechanics** ([`spread_common.py`](../src/qhfi/strategy/library/spread_common.py)) — `hysteresis_positions(z, entry_z, exit_z, warmup=0) -> {-1,0,+1}` (causal state machine; first `warmup` rows / non-finite z never open) and `scale_to_gross(raw, gross)` (each active row `Σ|w| = gross`). Used by `kalman_pairs` and `butterfly`.

---

## 4. Market-making quoters — [`strategy/library/mm/`](../src/qhfi/strategy/library/mm)

All subclass `QuotingStrategy` (`on_book(event, book) -> list[QuoteEvent]`), register in the separate `mm_registry`, and return `[]` on a NaN book. **Math in [MARKET_MAKING.md](MARKET_MAKING.md).** Common params: `q_max=100, quote_size=1, join_only=True, tick_size=0`.

<a id="mm-symmetric"></a>
### 4.1 `SymmetricMM` — [`mm/symmetric.py`](../src/qhfi/strategy/library/mm/symmetric.py)
Fixed-spread control baseline, no skew/signal. `half_spread_bps=5`. The reference the smarter quoters are measured against.

<a id="mm-linear"></a>
### 4.2 `LinearInventoryMM` — [`mm/linear_inventory.py`](../src/qhfi/strategy/library/mm/linear_inventory.py)
Scale-free (bps) base spread + linear inventory skew + OBI center tilt (+ optional vol-widening). `+ skew_bps=8, obi_alpha=0.5, vol_gain=0.0, sigma_window=100`.

<a id="mm-as"></a>
### 4.3 `AvellanedaStoikovMM` — [`mm/avellaneda_stoikov.py`](../src/qhfi/strategy/library/mm/avellaneda_stoikov.py)
Avellaneda–Stoikov reservation price + optimal spread, centered on an OBI/microprice fair value. `gamma=0.1, kappa=1.5, horizon=1.0, obi_alpha=0.5, inv_soften=True`.

<a id="mm-alpha"></a>
### 4.4 `AlphaQuoterMM` — [`mm/alpha_quoter.py`](../src/qhfi/strategy/library/mm/alpha_quoter.py)
LinearInventory + a **calibrated predictive OBI-drift** overlay (shifts fair value by predicted bps/OBI) + optional taker mode (crosses when edge beats crossing cost). `+ alpha_bps, alpha_gain=1.0, take_threshold_bps, taker_fee_bps=10`. With `alpha_bps=0` it reduces to `LinearInventoryMM`.

---

## 5. Supporting subsystems

### 5.1 Backtest engine — [`backtest/engine.py`](../src/qhfi/backtest/engine.py)
`BacktestEngine(cost_model=None, slippage=None, financing=None, sizing=None, execution=None, initial_equity=100_000.0).run(weights, prices, universe, open_prices=None, carry=None) -> BacktestResult`. `ExecutionConfig(signal_lag=1, fill=CLOSE, rebalance_threshold=0.0, allow_fractional=False)`. **Look-ahead guard:** `target = weights.reindex(...).shift(signal_lag)`. Per-day loop: variation margin → financing/carry → rebalance (weight→units, lot-round, trade delta, no-trade band) → fill (close/next-open, adverse slippage + commission) → carry income → re-mark equity. `BacktestResult` carries the full decomposition. **Tests:** [`test_backtest_engine.py`](../tests/test_backtest_engine.py). Cite [ARCHITECTURE.md §8](../ARCHITECTURE.md).

### 5.2 Validation — [`backtest/validation.py`](../src/qhfi/backtest/validation.py)
`walk_forward(strategy, prices, universe, engine, cfg=None)`; `WalkForwardConfig(train_days=504, test_days=126, step_days=126, purge_days=10)` — rolling train/test with a purge gap (no `embargo` field — that's on `ModelStrategyParams`). `concat_oos(results)` → contiguous, de-duplicated OOS returns. **Tests:** [`test_validation.py`](../tests/test_validation.py).

### 5.3 Cost / fill / financing
[`costs.py`](../src/qhfi/backtest/costs.py) — `CompositeCostModel` dispatches by asset class (CRYPTO Bps(10), EQUITY per-share, FX Bps(2), RATES Bps(1), …). [`fills.py`](../src/qhfi/backtest/fills.py) — `FillTiming(CLOSE|NEXT_OPEN)`, `SlippageModel(bps=5).fill_price(ref, side)=ref·(1+side·bps/1e4)`. [`financing.py`](../src/qhfi/backtest/financing.py) — `FinancingModel(short_borrow_bps=50, leverage_bps=100, cash_bps=0).daily_carry(...)`.

### 5.4 Event-driven engine
[`backtest/eventdriven/`](../src/qhfi/backtest/eventdriven) — `MarketEvent → SignalEvent → OrderEvent → FillEvent` via a heapq `EventLoop`, `Portfolio` mirrors the vectorized accounting; `WeightStrategyAdapter` runs vectorized strategies unchanged. **Full doc: [event_driven_engine.md](event_driven_engine.md).** **Tests:** [`test_eventdriven.py`](../tests/test_eventdriven.py).

### 5.5 Paper loops — [`trading/`](../src/qhfi/trading)
`PaperLoop.run_once` ([`loop.py`](../src/qhfi/trading/loop.py)): gap-fill bars → `generate_weights` → last row → risk-gate → `diff_to_orders` → submit (reuses the exact backtest contract; never raises on one bad order). `diff_to_orders` ([`reconcile.py`](../src/qhfi/trading/reconcile.py)): `target_qty = weight·equity/(price·mult)`, lot-round the delta, skip dust `< min_trade_notional` (25). `PaperQuotingLoop` ([`quoting_loop.py`](../src/qhfi/trading/quoting_loop.py)): MM paper stage + drawdown kill-switch + inventory cap. **Tests:** [`test_paper_loop.py`](../tests/test_paper_loop.py), [`test_reconcile.py`](../tests/test_reconcile.py).

### 5.6 Execution & allocator
[`execution/`](../src/qhfi/execution) — `Broker` protocol (`get_account/get_positions/submit`), `PaperBroker` (partial stubs), `AlpacaPaperBroker` (US equities via alpaca-py), `TWAP`/`POV` algorithms + `MarketImpactModel(η·√participation)`. [`portfolio/allocator.py`](../src/qhfi/portfolio/allocator.py) — `Allocator` protocol; `EqualWeightAllocator`/`VolTargetAllocator` both `NotImplementedError` stubs ("where the incubator becomes a fund").

---

## 6. Usage, math, design & gaps

```python
# frictionless backtest + lag check (tests/test_backtest_engine.py)
r = BacktestEngine(cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
                   financing=FinancingModel(0.0,0.0,0.0),
                   execution=ExecutionConfig(signal_lag=1, allow_fractional=True),
                   initial_equity=10_000.0).run(weights, prices, universe)   # lag=1 → no day-1 P&L

# end-to-end walk-forward OOS (tests/test_validation.py)
strat = FactorStrategy([MomentumFactor()], params=FactorStrategyParams(quantile=0.34))
folds = walk_forward(strat, prices, universe, BacktestEngine(),
                     WalkForwardConfig(train_days=200, test_days=50, step_days=50, purge_days=5))
oos = concat_oos(folds)                                                       # → Scorecard
```

**Scripts:** `scripts/demo_backtest.py`, `build_market_maker.py`, `calibrate_as_params.py`. **CLI:** `qhfi backtest run`, `qhfi paper run-once`, `qhfi mm` ([`cli.py`](../src/qhfi/cli.py)).

- **Math:** no-look-ahead → engine `shift(signal_lag)`; `long_short_weights` builds a disjoint-quantile dollar-neutral book; stat-arb trades hedged-spread z with hysteresis; `mdp` solves Bellman over regimes; overfitting defended by `walk_forward` + Deflated Sharpe.
- **Design:** strategy = pure function of data; one simulation path (paper/event reuse `generate_weights`); registry for zero-arg kinds, constructor-carried state for the rest; taxonomy wider than the library.
- **Gaps / stubs:** `Momentum.generate_weights`, both `Allocator`s, `PaperBroker.get_account`/`submit` (`NotImplementedError`); `value/quality/low_volatility/reversal/carry` are PLANNED; `ModelStrategy` ML path depends on the stubbed [`qhfi.models` predictive layer](models.md#predictive).
- **Documented elsewhere:** quoting math → [MARKET_MAKING.md](MARKET_MAKING.md); streaming engine → [event_driven_engine.md](event_driven_engine.md).
