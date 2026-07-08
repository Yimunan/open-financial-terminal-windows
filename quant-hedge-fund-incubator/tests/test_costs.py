"""Commission models: bps, equity per-share+min, futures per-contract, composite dispatch.
Commission only — slippage is the engine's SlippageModel."""

from __future__ import annotations

import pytest

from qhfi.backtest.costs import (
    BpsCostModel,
    CompositeCostModel,
    EquityCostModel,
    FuturesCostModel,
)
from qhfi.core.types import AssetClass, Instrument, InstrumentForm


def test_bps_is_flat_fraction_of_notional():
    ins = Instrument(id="BTC/USDT", asset_class=AssetClass.CRYPTO)
    assert BpsCostModel(10.0).cost(100_000, ins, price=50_000) == pytest.approx(100.0)


def test_equity_per_share_with_minimum():
    ins = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)
    m = EquityCostModel(per_share=0.005, min_ticket=1.0)
    # 1000 shares @ $200 = $200k notional → 1000 * 0.005 = $5
    assert m.cost(200_000, ins, price=200) == pytest.approx(5.0)
    # tiny trade floors at the per-ticket minimum
    assert m.cost(200, ins, price=200) == pytest.approx(1.0)


def test_futures_per_contract_uses_multiplier():
    es = Instrument(id="ES", asset_class=AssetClass.COMMODITY,
                    form=InstrumentForm.FUTURE, contract_multiplier=50.0)
    # 4 contracts @ 5000 × 50 mult = $1,000,000 notional → 4 * $2 = $8
    assert FuturesCostModel(per_contract=2.0).cost(1_000_000, es, price=5000) == pytest.approx(8.0)


def test_composite_dispatches_by_asset_class():
    comp = CompositeCostModel()
    eq = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)
    cr = Instrument(id="BTC/USDT", asset_class=AssetClass.CRYPTO)
    assert comp.cost(200_000, eq, 200) == pytest.approx(5.0)         # EquityCostModel
    assert comp.cost(100_000, cr, 50_000) == pytest.approx(100.0)   # BpsCostModel(10bps)
