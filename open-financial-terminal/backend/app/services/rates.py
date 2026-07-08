"""Rates data service — the qhfi lake's interest-rate products for the Rates module.

Two products, both read-only over the qhfi parquet lake (produced by qhfi's pull scripts):

* the US **Treasury yield curve** (wide dates×tenor panel under ``rates/``) — reuses the Macro
  module's curve serializer so the term structure stays defined in exactly one place;
* the CME **Treasury futures complex** (ZT/ZF/ZN/ZB/UB/ZQ, daily OHLCV under ``market/rates/``).

The futures live in the qhfi lake, not the terminal's own bar cache, so they are read through a
``DataStore`` rooted at ``qhfi_lake_dir`` (see ``deps.get_rates_futures_store``) — a pure lake read,
no provider refresh (the background DataRefreshRunner keeps the lake current).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from qhfi.core.types import AssetClass, Instrument
from qhfi.data.base import DataStore
from qhfi.data.rates import RatesStore

from app.services import macro as macro_svc
from app.services.market import quote_from_bars, to_candles

#: CME Treasury futures complex, short → long maturity. Mirrors config/instruments/rates_futures.yaml
#: (contract_multiplier = $ per 1.0 price point; modified_duration = approx CTD duration for DV01
#: sizing) plus the pull script's display names.
FUTURES_ORDER: list[str] = ["ZQ", "ZT", "ZF", "ZN", "ZB", "UB"]
FUTURES_META: dict[str, dict] = {
    "ZQ": {"name": "30D Fed Funds", "tenor": "1M", "mult": 4167, "mod_dur": 0.08},
    "ZT": {"name": "2Y T-Note", "tenor": "2Y", "mult": 2000, "mod_dur": 1.9},
    "ZF": {"name": "5Y T-Note", "tenor": "5Y", "mult": 1000, "mod_dur": 4.4},
    "ZN": {"name": "10Y T-Note", "tenor": "10Y", "mult": 1000, "mod_dur": 7.8},
    "ZB": {"name": "30Y T-Bond", "tenor": "30Y", "mult": 1000, "mod_dur": 15.5},
    "UB": {"name": "Ultra Bond", "tenor": "30Y+", "mult": 1000, "mod_dur": 19.0},
}


def curve(store: RatesStore, start: date | None, end: date | None) -> dict:
    """US Treasury yield curve — delegates to the Macro serializer (single source for tenor logic)."""
    return macro_svc.rates_curve(store, start, end)


def _load(store: DataStore, symbol: str) -> pd.DataFrame | None:
    """Read one rates-future's cached daily bars from the lake, or None if absent/empty."""
    ins = Instrument(id=symbol, asset_class=AssetClass.RATES)
    if not store.has(ins):
        return None
    bars = store.load(ins)
    return bars if not bars.empty else None


def futures_grid(store: DataStore, spark_points: int = 60) -> dict:
    """Latest quote + a recent close sparkline for each Treasury future in the lake."""
    rows = []
    for symbol in FUTURES_ORDER:
        bars = _load(store, symbol)
        if bars is None:
            continue
        meta = FUTURES_META[symbol]
        rows.append({
            "symbol": symbol,
            "name": meta["name"],
            "tenor": meta["tenor"],
            "contract_multiplier": meta["mult"],
            "modified_duration": meta["mod_dur"],
            "quote": quote_from_bars(bars),
            "spark": [round(float(v), 4) for v in bars["close"].tail(spark_points)],
        })
    return {"futures": rows}


def futures_bars(store: DataStore, symbol: str, start: date | None, end: date | None) -> dict | None:
    """Daily candlestick + volume payload for one Treasury future, or None if unknown/absent."""
    symbol = symbol.upper()
    if symbol not in FUTURES_META:
        return None
    bars = _load(store, symbol)
    if bars is None:
        return None
    if start is not None:
        bars = bars.loc[str(start):]
    if end is not None:
        bars = bars.loc[:str(end)]
    if bars.empty:
        return None
    meta = FUTURES_META[symbol]
    payload = to_candles(bars, intraday=False)
    payload["symbol"] = symbol
    payload["name"] = meta["name"]
    payload["tenor"] = meta["tenor"]
    payload["quote"] = quote_from_bars(bars)
    return payload
