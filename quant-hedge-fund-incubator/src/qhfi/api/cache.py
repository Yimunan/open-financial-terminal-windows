"""TTL response cache — avoids re-hitting an API for identical idempotent reads within a
short window (e.g. repeated reference-data or quote lookups). Clock injectable."""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(self, ttl: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self.clock = clock
        self._store: dict[Hashable, tuple[Any, float]] = {}

    def get(self, key: Hashable) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires = item
        if self.clock() >= expires:
            del self._store[key]
            return None
        return value

    def set(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        self._store[key] = (value, self.clock() + (self.ttl if ttl is None else ttl))

    def clear(self) -> None:
        self._store.clear()
