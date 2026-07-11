"""The strategy taxonomy registry is well-formed, classifies the strategy space across all six
dimensions, and stays consistent with the runtime strategy registry (every implemented kind is
actually registered, and vice versa)."""

from __future__ import annotations

import qhfi.strategy.library  # noqa: F401  — importing populates strategy.registry
from qhfi.core.types import AssetClass
from qhfi.strategy.registry import all_names
from qhfi.strategy.taxonomy import (
    STRATEGIES,
    DataInput,
    Exposure,
    SignalAxis,
    Status,
    StrategyStyle,
    by_asset_class,
    by_data_input,
    by_status,
    by_style,
    describe,
    get,
)


def test_registry_is_well_formed():
    names = [k.name for k in STRATEGIES]
    assert len(names) == len(set(names))                  # unique kind names
    for k in STRATEGIES:
        assert k.asset_classes                            # at least one asset class
        assert isinstance(k.style, StrategyStyle)
    assert describe()                                     # serializable rows
    assert len(describe()) == len(STRATEGIES)


def test_every_registered_strategy_is_classified():
    """Every strategy in the runtime string-registry has a taxonomy entry, classified as a
    runnable kind (LIVE/STUB, never PLANNED). The taxonomy may ALSO classify real strategies
    that aren't string-registered because they need constructor args (e.g. FactorStrategy,
    like factors.Alpha/FundamentalFactor) — so this is a subset, not an equality, check."""
    registered = set(all_names())                         # {"model", "momentum"}
    classified = {k.name for k in STRATEGIES}
    assert registered <= classified                       # no unclassified runtime strategy
    for name in registered:
        assert get(name).status in (Status.LIVE, Status.STUB)
    # FactorStrategy is live-but-not-string-registered (needs a `factors=[...]` arg):
    assert "factor" not in registered and get("factor").status is Status.LIVE


def test_factor_is_the_live_multifactor_workhorse():
    factor = get("factor")
    assert factor.status is Status.LIVE and factor.implemented
    assert factor.style is StrategyStyle.MULTI_FACTOR
    assert factor.signal_axis is SignalAxis.CROSS_SECTIONAL
    assert factor.exposure is Exposure.DOLLAR_NEUTRAL


def test_momentum_is_a_stub_not_runnable():
    momentum = get("momentum")
    assert momentum.status is Status.STUB
    assert not momentum.implemented                       # registered, but core is NotImplementedError


def test_planned_kinds_name_their_enabling_factor():
    for k in by_status(Status.PLANNED):
        assert k.enabling_factors                         # a planned family must point at a factor
    assert {k.name for k in by_status(Status.PLANNED)} >= {"value", "quality", "carry"}


def test_by_style_filters():
    assert {k.name for k in by_style(StrategyStyle.STATISTICAL)} == {"model"}
    assert all(k.style is StrategyStyle.MOMENTUM for k in by_style(StrategyStyle.MOMENTUM))


def test_by_asset_class_filters():
    equity = {k.name for k in by_asset_class(AssetClass.EQUITY)}
    assert {"factor", "value", "quality"} <= equity
    # carry is the only kind reaching into rates/FX/commodity
    assert "carry" in {k.name for k in by_asset_class(AssetClass.COMMODITY)}
    assert "factor" not in {k.name for k in by_asset_class(AssetClass.COMMODITY)}


def test_data_input_dimension_and_its_declared_but_empty_values():
    """Every kind declares a data input, describe() carries it, and the enum intentionally holds
    inputs reached by no existing OR planned kind (order_book, alternative) — the intended space,
    wider than what's built, mirroring the unused `trend` style and `intraday`/`weekly` horizons."""
    for k in STRATEGIES:
        assert isinstance(k.data_input, DataInput)
    assert all("data_input" in row for row in describe())

    used = {k.data_input for k in STRATEGIES}
    empty = set(DataInput) - used
    assert {DataInput.ORDER_BOOK, DataInput.ALTERNATIVE} <= empty   # declared but no kind yet
    assert not by_data_input(DataInput.ORDER_BOOK)                  # no strategy uses it

    # spot-checks of the classification
    assert {k.name for k in by_data_input(DataInput.FUNDAMENTALS)} == {"value", "quality"}
    assert get("carry").data_input is DataInput.CARRY
    assert get("barra_minvar").data_input is DataInput.REFERENCE
    assert get("factor").data_input is DataInput.PRICE           # price-only by default
