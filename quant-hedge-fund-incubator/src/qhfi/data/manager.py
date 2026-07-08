# NOTE: Phase-2 reconstruction. DataManager orchestrates providers + the parquet DataStore:
#   update(universe, span)  -> fetch each instrument's daily bars, merge with cache, persist
#   get(instrument, span)   -> cached (refreshing if absent) OHLCV for one instrument
#   get_panel(u, field, span) -> wide frame: index=dates, columns=instrument ids, one field
# Signatures match app/services/market.py (update + store.has/load) and
# app/services/screener.py etc. (get_panel(universe, "close", span)).
from __future__ import annotations

import pandas as pd

from .base import DataStore, _empty_bars


def _as_instruments(target):
    insts = getattr(target, "instruments", None)
    if insts is not None:
        return list(insts)
    if isinstance(target, (list, tuple, set)):
        return list(target)
    return [target]


def _merge(existing, fresh):
    if existing is None or len(existing) == 0:
        return fresh
    if fresh is None or len(fresh) == 0:
        return existing
    combined = pd.concat([existing, fresh])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined


def _slice(frame, span):
    if span is None or frame is None or len(frame) == 0:
        return frame
    try:
        return frame.loc[str(span.start):str(span.end)]
    except Exception:
        return frame


class DataManager:
    def __init__(self, store=None, providers=None, *args, **kwargs):
        self.store = store if store is not None else DataStore(kwargs.get("root", "."))
        self.providers = providers or {}

    def _provider(self, instrument):
        return self.providers.get(getattr(instrument, "asset_class", None))

    def update(self, target, span=None, *a, **k):
        """Fetch fresh daily bars for each instrument and merge into the lake. Incremental:
        merges with any cached history so repeat calls only extend the tail."""
        n = 0
        for inst in _as_instruments(target):
            prov = self._provider(inst)
            if prov is None:
                continue
            try:
                fresh = prov.fetch_daily(inst, span)
            except Exception:
                continue
            if fresh is None or len(fresh) == 0:
                continue
            if self.store is not None:
                existing = self.store.load(inst) if self.store.has(inst) else None
                self.store.save(inst, _merge(existing, fresh))
            n += 1
        return n

    def get(self, instrument, span=None, *a, **k):
        if self.store is None:
            return _empty_bars()
        if not self.store.has(instrument):
            self.update(instrument, span)
        return _slice(self.store.load(instrument), span)

    def get_panel(self, universe, field="close", span=None, *a, **k):
        series = {}
        for inst in _as_instruments(universe):
            if self.store is not None and self.store.has(inst):
                bars = self.store.load(inst)
            else:
                bars = self.get(inst, span)
            if bars is None or len(bars) == 0 or field not in bars.columns:
                continue
            series[getattr(inst, "id", str(inst))] = bars[field]
        if not series:
            return pd.DataFrame()
        return _slice(pd.DataFrame(series).sort_index(), span)
