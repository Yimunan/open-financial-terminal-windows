"""Book-replay data handler — the source of ``BookEvent``s (and optional ``TradeEvent``s)
for the quoting engine.

Consumes the long-format ``OrderBookStore`` frame per symbol (``snapshot_ts, side, level,
price, amount``), reconstructs the top-N book at each snapshot, and yields one ``BookEvent``
per ``(snapshot_ts, symbol)`` in non-decreasing timestamp order (heap-merged across symbols).
``mid``/``microprice``/``obi`` are computed here so the strategy and matcher get them for free.
A trade tape, when supplied, is interleaved as ``TradeEvent``s so the matcher can fill resting
quotes against crossing prints between book updates.
"""

from __future__ import annotations

import heapq
from typing import Iterator

import pandas as pd

from qhfi.backtest.eventdriven.events import BookEvent, TradeEvent
from qhfi.data.microstructure import microprice, order_book_imbalance


class BookReplayDataHandler:
    """Stream ``BookEvent``/``TradeEvent`` from recorded L2 snapshots (+ optional trades)."""

    def __init__(
        self,
        books: dict[str, pd.DataFrame],
        trades: dict[str, pd.DataFrame] | None = None,
        levels: int = 10,
        decay: float = 0.0,
    ) -> None:
        self.books = books
        self.trades = trades or {}
        self.levels = levels
        self.decay = decay

    def _book_events(self, sym: str, df: pd.DataFrame) -> Iterator[BookEvent]:
        df = df[df["level"] < self.levels]
        for ts, grp in df.groupby("snapshot_ts", sort=True):
            bids = [(float(p), float(a)) for p, a in
                    grp[grp["side"] == "bid"].sort_values("level")[["price", "amount"]].to_numpy()]
            asks = [(float(p), float(a)) for p, a in
                    grp[grp["side"] == "ask"].sort_values("level")[["price", "amount"]].to_numpy()]
            if not bids or not asks:
                continue
            mid = (bids[0][0] + asks[0][0]) / 2.0
            mp = microprice(bids[0][0], asks[0][0], bids[0][1], asks[0][1])
            obi = order_book_imbalance([a for _, a in bids], [a for _, a in asks], self.decay)
            yield BookEvent(
                timestamp=pd.Timestamp(ts, unit="ms", tz="UTC"), instrument=sym,
                bids=tuple(bids), asks=tuple(asks), mid=mid, microprice=mp, obi=obi,
            )

    def _trade_events(self, sym: str, df: pd.DataFrame) -> Iterator[TradeEvent]:
        for ts, price, size, side in df[["ts", "price", "size", "side"]].to_numpy():
            yield TradeEvent(timestamp=pd.Timestamp(ts, unit="ms", tz="UTC"), instrument=sym,
                             price=float(price), size=float(size), side=str(side))

    def stream(self) -> Iterator[BookEvent | TradeEvent]:
        sources = [self._book_events(s, df) for s, df in self.books.items()]
        sources += [self._trade_events(s, df) for s, df in self.trades.items()]
        # Heap-merge by (timestamp, PRIORITY); a counter keeps it stable and avoids comparing events.
        yield from heapq.merge(*sources, key=lambda e: (e.timestamp, e.PRIORITY))
