"""Strategy taxonomy — a formal classification of the *kinds* of strategies the incubator
runs, the trading-side mirror of :mod:`qhfi.data.taxonomy`.

Seven dimensions tag every strategy kind: **style** (the alpha family), **asset classes** (what
it trades), **horizon** (holding period), **exposure** (how the book is constructed /
neutralized), **signal axis** (cross-sectional vs time-series), **data input** (what data it needs
beyond a close-price panel), and **status** (live/stub/planned). The ``STRATEGIES`` registry is the
single source of truth for the strategy *space* — what families exist, which are actually
implemented vs. planned, and how each is built.

Like ``horizon`` (``intraday``/``weekly`` sit unused) and ``style`` (``trend`` has no kind yet),
the enums deliberately encode the *intended* space: some values are declared but reached by no
existing or planned kind — e.g. ``DataInput.ORDER_BOOK`` and ``DataInput.ALTERNATIVE``.

Names of LIVE/STUB kinds match the keys in :mod:`qhfi.strategy.registry` (the classes reachable
via ``strategy.registry.get``); PLANNED kinds map a known alpha family to the enabling factor
that already exists in :mod:`qhfi.factors.library` but has no dedicated strategy yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from qhfi.core.types import AssetClass


# ── style dimension ──────────────────────────────────────────────────────────────
class StrategyStyle(str, Enum):
    MOMENTUM = "momentum"             # winners keep winning (cross-sectional trend)
    VALUE = "value"                   # cheap-vs-expensive on a fundamental ratio
    CARRY = "carry"                   # earn the yield for holding (funding/roll/coupon)
    REVERSAL = "reversal"             # short-horizon mean reversion
    QUALITY = "quality"               # profitability / soundness
    LOW_VOLATILITY = "low_volatility" # the low-vol / low-beta anomaly
    MULTI_FACTOR = "multi_factor"     # a blend of standardized factors
    STATISTICAL = "statistical"       # ML / statistical forecaster over features
    STAT_ARB = "stat_arb"             # statistical arbitrage (pairs / cointegration / spreads)
    TREND = "trend"                   # time-series trend (managed-futures style)
    MACRO = "macro"                   # driven by macro/rates state
    RISK_BASED = "risk_based"         # risk-model-driven construction (min-var / factor risk)


# ── horizon dimension ────────────────────────────────────────────────────────────
class Horizon(str, Enum):
    INTRADAY = "intraday"   # sub-day
    SWING = "swing"         # days (the daily engine's natural cadence)
    WEEKLY = "weekly"       # ~1–4 weeks
    MONTHLY = "monthly"     # a month+ (slow factors: value, quality)


# ── exposure / neutrality dimension ──────────────────────────────────────────────
class Exposure(str, Enum):
    MARKET_NEUTRAL = "market_neutral"  # dollar- and beta-neutral long/short
    DOLLAR_NEUTRAL = "dollar_neutral"  # equal long/short notional (default L/S book here)
    BETA_NEUTRAL = "beta_neutral"      # net-beta hedged, not necessarily dollar-neutral
    LONG_SHORT = "long_short"          # long/short, no explicit neutralization
    LONG_ONLY = "long_only"            # no shorts


# ── signal-axis dimension ────────────────────────────────────────────────────────
class SignalAxis(str, Enum):
    CROSS_SECTIONAL = "cross_sectional"  # rank instruments against each other on a date
    TIME_SERIES = "time_series"          # each instrument judged vs. its own history


# ── data-input dimension ─────────────────────────────────────────────────────────
class DataInput(str, Enum):
    """Extra data a strategy needs beyond a close-price panel. Deliberately wider than what's
    built: ``ORDER_BOOK`` and ``ALTERNATIVE`` are declared but reached by no existing/planned kind."""
    PRICE = "price"                # OHLCV panel only — nothing beyond the price grid
    FUNDAMENTALS = "fundamentals"  # point-in-time financial statements (E/P, ROE, …)
    REFERENCE = "reference"        # security master: sector/industry classification + market cap
    CARRY = "carry"                # funding / roll / coupon yield per asset class
    ORDER_BOOK = "order_book"      # L2 microstructure — used by market-making, outside this taxonomy
    ALTERNATIVE = "alternative"    # sentiment / positioning / alt data — a planned research direction


# ── status dimension (mirrors data.taxonomy.Status) ──────────────────────────────
class Status(str, Enum):
    LIVE = "live"        # implemented and runnable
    STUB = "stub"        # contract present, core raises NotImplementedError
    PLANNED = "planned"  # a known family with an enabling factor but no strategy yet


@dataclass(frozen=True)
class StrategyKind:
    name: str
    style: StrategyStyle
    asset_classes: tuple[AssetClass, ...]
    horizon: Horizon
    exposure: Exposure
    signal_axis: SignalAxis
    status: Status
    data_input: DataInput = DataInput.PRICE  # extra data beyond a close panel the strategy needs
    enabling_factors: tuple[str, ...] = ()  # factor names (factors.registry) it builds on
    notes: str = ""

    @property
    def implemented(self) -> bool:
        """A runnable strategy reachable via ``strategy.registry.get`` (LIVE only — a STUB is
        registered but raises NotImplementedError)."""
        return self.status is Status.LIVE


# ── the registry ─────────────────────────────────────────────────────────────────
# LIVE/STUB names == strategy.registry keys; PLANNED names document the intended space.
STRATEGIES: list[StrategyKind] = [
    StrategyKind(
        "factor", StrategyStyle.MULTI_FACTOR, (AssetClass.EQUITY, AssetClass.CRYPTO),
        Horizon.SWING, Exposure.DOLLAR_NEUTRAL, SignalAxis.CROSS_SECTIONAL, Status.LIVE,
        enabling_factors=("momentum", "volatility", "reversal", "value", "quality"),
        notes="The factor→weights bridge: winsorize→zscore→sector-neutralize→blend, then "
              "long/short quantile selection. Most cross-sectional strategies are an instance "
              "of this with different factor choices.",
    ),
    StrategyKind(
        "model", StrategyStyle.STATISTICAL, (AssetClass.EQUITY, AssetClass.CRYPTO),
        Horizon.SWING, Exposure.DOLLAR_NEUTRAL, SignalAxis.CROSS_SECTIONAL, Status.LIVE,
        enabling_factors=("momentum", "volatility", "reversal"),
        notes="Feeds standardized factors into a trained sklearn estimator; predicted forward "
              "returns are the score. Served (pre-fit) or walk-forward (refit per fold) modes.",
    ),
    StrategyKind(
        "mdp", StrategyStyle.MACRO, (AssetClass.EQUITY, AssetClass.CRYPTO),
        Horizon.SWING, Exposure.LONG_ONLY, SignalAxis.TIME_SERIES, Status.LIVE,
        notes="Regime-switching dynamic allocation solved as a Markov Decision Process: a "
              "Gaussian-mixture market regime is the state, the risky-book fraction is the action, "
              "and value iteration over the regime transition matrix picks the optimal exposure "
              "per regime (de-risk in volatile regimes). Scales a long-only risky book; not a "
              "cross-sectional alpha.",
    ),
    StrategyKind(
        "kalman_pairs", StrategyStyle.STAT_ARB, (AssetClass.EQUITY, AssetClass.CRYPTO),
        Horizon.SWING, Exposure.DOLLAR_NEUTRAL, SignalAxis.TIME_SERIES, Status.LIVE,
        notes="Statistical-arbitrage pairs trade: a Kalman filter tracks a time-varying hedge "
              "ratio between two instruments and trades the mean reversion of the filtered spread's "
              "z-score (dollar-neutral 2-leg book). Like FactorStrategy it carries its inputs (the "
              "pair) so it is not string-registered.",
    ),
    StrategyKind(
        "butterfly", StrategyStyle.STAT_ARB, (AssetClass.EQUITY, AssetClass.CRYPTO),
        Horizon.SWING, Exposure.DOLLAR_NEUTRAL, SignalAxis.TIME_SERIES, Status.LIVE,
        notes="Three-leg price-butterfly stat-arb: trade the mean reversion of a belly vs. its two "
              "wings (the second price difference). Spread is either Kalman-regression-weighted "
              "(belly on wings → 1:-b1:-b2) or the fixed structural butterfly (w1-2·belly+w2). The "
              "3-leg generalization of kalman_pairs; carries its legs so it is not string-registered.",
    ),
    StrategyKind(
        "barra_minvar", StrategyStyle.RISK_BASED, (AssetClass.EQUITY,),
        Horizon.MONTHLY, Exposure.LONG_ONLY, SignalAxis.CROSS_SECTIONAL, Status.LIVE,
        data_input=DataInput.REFERENCE,
        notes="Minimum-variance book on the Barra cross-sectional risk model: each month fit "
              "Σ = X F Xᵀ + diag(Δ) (standardized style factors + GICS industry dummies, √ADV WLS) "
              "on a trailing window and hold long-only min-var weights. Carries its MarketPanels "
              "(needs volume for the cap proxy) so it is not string-registered.",
    ),
    StrategyKind(
        "momentum", StrategyStyle.MOMENTUM, (AssetClass.EQUITY, AssetClass.CRYPTO),
        Horizon.SWING, Exposure.DOLLAR_NEUTRAL, SignalAxis.CROSS_SECTIONAL, Status.STUB,
        enabling_factors=("momentum",),
        notes="Reference single-factor template (ranked cross-sectional momentum). Core is a "
              "NotImplementedError stub — the codegen agent's worked example.",
    ),
    # ── planned: families with an enabling factor but no dedicated strategy yet ──
    StrategyKind(
        "value", StrategyStyle.VALUE, (AssetClass.EQUITY,),
        Horizon.MONTHLY, Exposure.DOLLAR_NEUTRAL, SignalAxis.CROSS_SECTIONAL, Status.PLANNED,
        data_input=DataInput.FUNDAMENTALS,
        enabling_factors=("value",),
        notes="Cheapness (E/P, B/P) via ValueFactor; awaits broader PIT fundamentals coverage.",
    ),
    StrategyKind(
        "quality", StrategyStyle.QUALITY, (AssetClass.EQUITY,),
        Horizon.MONTHLY, Exposure.DOLLAR_NEUTRAL, SignalAxis.CROSS_SECTIONAL, Status.PLANNED,
        data_input=DataInput.FUNDAMENTALS,
        enabling_factors=("quality",),
        notes="Profitability/soundness (ROE, margins, low leverage) via QualityFactor.",
    ),
    StrategyKind(
        "low_volatility", StrategyStyle.LOW_VOLATILITY, (AssetClass.EQUITY,),
        Horizon.SWING, Exposure.DOLLAR_NEUTRAL, SignalAxis.CROSS_SECTIONAL, Status.PLANNED,
        enabling_factors=("volatility",),
        notes="The low-vol anomaly via VolatilityFactor (direction=-1).",
    ),
    StrategyKind(
        "reversal", StrategyStyle.REVERSAL, (AssetClass.EQUITY, AssetClass.CRYPTO),
        Horizon.SWING, Exposure.DOLLAR_NEUTRAL, SignalAxis.CROSS_SECTIONAL, Status.PLANNED,
        enabling_factors=("reversal",),
        notes="Short-horizon mean reversion via ShortTermReversalFactor.",
    ),
    StrategyKind(
        "carry", StrategyStyle.CARRY,
        (AssetClass.CRYPTO, AssetClass.RATES, AssetClass.FX, AssetClass.COMMODITY),
        Horizon.SWING, Exposure.LONG_SHORT, SignalAxis.CROSS_SECTIONAL, Status.PLANNED,
        data_input=DataInput.CARRY,
        enabling_factors=("carry",),
        notes="Earn the yield for holding (funding/roll/coupon) via CarryFactor; blocked on the "
              "carry/funding/roll-yield data panel (still a stub).",
    ),
]

_BY_NAME = {k.name: k for k in STRATEGIES}


# ── views ────────────────────────────────────────────────────────────────────────
def get(name: str) -> StrategyKind:
    if name not in _BY_NAME:
        raise KeyError(f"unknown strategy kind {name!r}; known: {sorted(_BY_NAME)}")
    return _BY_NAME[name]


def by_style(style: StrategyStyle) -> list[StrategyKind]:
    return [k for k in STRATEGIES if k.style is style]


def by_asset_class(asset_class: AssetClass) -> list[StrategyKind]:
    return [k for k in STRATEGIES if asset_class in k.asset_classes]


def by_status(status: Status) -> list[StrategyKind]:
    return [k for k in STRATEGIES if k.status is status]


def by_data_input(data_input: DataInput) -> list[StrategyKind]:
    return [k for k in STRATEGIES if k.data_input is data_input]


def describe() -> list[dict]:
    return [
        {"name": k.name, "style": k.style.value,
         "asset_classes": ",".join(a.value for a in k.asset_classes),
         "horizon": k.horizon.value, "exposure": k.exposure.value,
         "signal_axis": k.signal_axis.value, "status": k.status.value,
         "data_input": k.data_input.value,
         "factors": ",".join(k.enabling_factors)}
        for k in STRATEGIES
    ]
