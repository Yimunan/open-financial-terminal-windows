"""Universe YAML loader — parses the committed pool configs, coercing nested equity meta."""

from __future__ import annotations

from pathlib import Path

from qhfi.core.types import AssetClass
from qhfi.core.universe_io import load_universe, save_universe

CONFIG = Path(__file__).resolve().parents[1] / "config" / "instruments"


def test_loads_equity_sector_pool():
    uni = load_universe(CONFIG / "equity_sectors.yaml")
    assert uni.name == "equity_sectors"
    assert len(uni.instruments) >= 50
    assert all(i.asset_class is AssetClass.EQUITY for i in uni.instruments)

    # equity meta coerced; sectors usable for neutralization
    assert uni.by_id("AAPL").sector == "Information Technology"
    sectors = set(uni.groups("gics_sector").values())
    assert len(sectors) == 11                      # all GICS sectors represented
    # each sector has enough names for cross-sectional neutralization
    counts = {s: list(uni.groups().values()).count(s) for s in sectors}
    assert min(counts.values()) >= 4


def test_loads_rates_futures_with_ficc_properties():
    from qhfi.core.types import InstrumentForm, RiskBasis

    uni = load_universe(CONFIG / "rates_futures.yaml")
    assert all(i.asset_class is AssetClass.RATES for i in uni.instruments)
    zn = uni.by_id("ZN")
    assert zn.form is InstrumentForm.FUTURE
    assert zn.is_margined                                   # futures → margined
    assert zn.risk_basis is RiskBasis.DV01                  # rates → DV01-sized
    assert zn.modified_duration == 7.8 and zn.contract_multiplier == 1000


def test_loads_crypto_pool_and_roundtrips(tmp_path):
    uni = load_universe(CONFIG / "crypto_majors.yaml")
    assert all(i.asset_class is AssetClass.CRYPTO for i in uni.instruments)

    out = tmp_path / "rt.yaml"
    save_universe(uni, out)
    assert load_universe(out).ids == uni.ids       # round-trip preserves ids
