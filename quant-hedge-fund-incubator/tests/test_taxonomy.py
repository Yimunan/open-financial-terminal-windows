"""The data taxonomy registry is well-formed and encodes the key invariants (fundamentals
must be point-in-time; reference data is a current snapshot)."""

from __future__ import annotations

from qhfi.data.taxonomy import (
    DATASETS,
    SEC_EDGAR,
    YFINANCE,
    DataDomain,
    PITDiscipline,
    SourceTier,
    by_domain,
    by_source,
    describe,
)


def test_registry_is_well_formed():
    names = [d.name for d in DATASETS]
    assert len(names) == len(set(names))                 # unique dataset names
    assert {d.domain for d in DATASETS} == set(DataDomain)  # every domain represented
    for d in DATASETS:
        assert d.asset_classes                            # at least one asset class
        assert d.sources                                  # at least one source
        assert describe()                                 # serializable rows


def test_source_is_a_structured_tiered_dimension():
    # EDGAR is the primary, PIT-capable system-of-record; yfinance an aggregator, not PIT
    assert SEC_EDGAR.tier is SourceTier.PRIMARY and SEC_EDGAR.primary and SEC_EDGAR.pit_capable
    assert YFINANCE.tier is SourceTier.AGGREGATOR and not YFINANCE.pit_capable


def test_fundamentals_authoritative_is_edgar_over_yfinance():
    fundamentals = next(d for d in DATASETS if d.name == "fundamentals")
    # both sources listed, but EDGAR (PRIMARY) is the system-of-record
    assert {s.key for s in fundamentals.sources} == {"sec_edgar", "yfinance"}
    assert fundamentals.authoritative is SEC_EDGAR


def test_by_source_filters():
    edgar_datasets = {d.name for d in by_source("sec_edgar")}
    assert {"fundamentals", "xbrl_facts", "original_filings"} <= edgar_datasets
    assert "fundamentals" in {d.name for d in by_source("yfinance")}   # also a yfinance source


def test_national_agency_sources_registered():
    from qhfi.data.taxonomy import SOURCES
    agencies = {"bea", "bls", "census", "treasury", "eurostat", "ons", "nbs", "boj", "rba"}
    assert agencies <= set(SOURCES)                              # all registered
    assert all(SOURCES[a].tier is SourceTier.PRIMARY for a in agencies)   # official primary sources
    # macro_series lists them as origins
    assert agencies <= {s.key for s in by_source("ons")[0].sources} | {"ons"}
    assert {d.name for d in by_source("boj")} == {"macro_series"}


def test_pit_invariants():
    fundamentals = next(d for d in DATASETS if d.name == "fundamentals")
    assert fundamentals.pit is PITDiscipline.POINT_IN_TIME   # backtest-safety contract

    ref = next(d for d in DATASETS if d.name == "instrument_reference")
    assert ref.pit is PITDiscipline.SNAPSHOT                 # current-only → survivorship caveat


def test_by_domain_filters():
    assert all(d.domain is DataDomain.MARKET for d in by_domain(DataDomain.MARKET))
    assert {d.name for d in by_domain(DataDomain.DERIVED)} == {
        "factors", "manager_graph_13f", "manager_graph_nodes_13f"}


def test_manager_graph_datasets_are_derived_and_pit():
    derived = {d.name: d for d in by_domain(DataDomain.DERIVED)}
    for name in ("manager_graph_13f", "manager_graph_nodes_13f"):
        assert derived[name].pit is PITDiscipline.POINT_IN_TIME   # backtest-safe like its parent
        assert derived[name].authoritative.key == "computed"      # derived in-house
