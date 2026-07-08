"""Token-bucket rate limiter — keeps the framework under each venue's request ceiling.

``rate`` tokens refill per second up to ``burst``; ``acquire`` consumes one, sleeping only
if the bucket is dry. The ``clock`` and ``sleep`` callables are injectable so behavior is
deterministically testable (a fake clock whose ``sleep`` advances time).
"""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        rate: float,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = rate
        self.capacity = burst if burst is not None else max(1, int(rate))
        self.tokens = float(self.capacity)
        self.clock = clock
        self.sleep = sleep
        self._last = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now

    def acquire(self, n: int = 1) -> float:
        """Block until ``n`` tokens are available; return total seconds waited."""
        waited = 0.0
        while True:
            self._refill()
            if self.tokens >= n:
                self.tokens -= n
                return waited
            wait = (n - self.tokens) / self.rate
            self.sleep(wait)
            waited += wait
