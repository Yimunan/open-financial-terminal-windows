"""Unit tests for the sector-exposure breakdown in `backtest._sector_exposure`.

`_sector_exposure` only reads `universe.instruments` (each with `.id` and `.sector`), so simple
namespace fakes exercise the aggregation logic without coupling to the qhfi Instrument type. No
network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from app.services.backtest import _sector_exposure


def _uni(*pairs: tuple[str, str | None]) -> SimpleNamespace:
    return SimpleNamespace(instruments=[SimpleNamespace(id=s, sector=sec) for s, sec in pairs])


def test_aggregates_signed_weight_by_sector():
    uni = _uni(("AAA", "Tech"), ("BBB", "Tech"), ("CCC", "Energy"), ("DDD", "Energy"))
    w = pd.Series({"AAA": 0.3, "BBB": 0.2, "CCC": -0.25, "DDD": -0.25})
    out = _sector_exposure(w, uni)
    by = {r["sector"]: r["net"] for r in out}
    assert by["Tech"] == 50.0     # 0.3 + 0.2
    assert by["Energy"] == -50.0  # short book
    assert out[0]["sector"] == "Tech" and out[-1]["sector"] == "Energy"  # sorted desc by net


def test_missing_symbol_bucketed_unknown_but_real_sectors_kept():
    uni = _uni(("AAA", "Tech"))
    w = pd.Series({"AAA": 0.5, "ZZZ": 0.5})  # ZZZ not in the universe
    out = _sector_exposure(w, uni)
    by = {r["sector"]: r["net"] for r in out}
    assert by["Tech"] == 50.0 and by["Unknown"] == 50.0


def test_no_sector_tags_returns_none():
    uni = _uni(("BTC/USDT", None), ("ETH/USDT", None))
    w = pd.Series({"BTC/USDT": 0.5, "ETH/USDT": 0.5})
    assert _sector_exposure(w, uni) is None


def test_empty_weights_returns_none():
    assert _sector_exposure(pd.Series(dtype=float), _uni(("AAA", "Tech"))) is None
