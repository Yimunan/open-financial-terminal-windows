"""Circuit breaker — stops hammering a dead endpoint and fails fast instead.

States: CLOSED (normal) → OPEN (after ``failure_threshold`` consecutive failures; calls
rejected immediately) → HALF_OPEN (after ``reset_timeout``; one trial allowed) → CLOSED on
success or back to OPEN on failure. Clock injectable for deterministic tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the breaker is open."""


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.clock = clock
        self.state = "closed"
        self.failures = 0
        self._opened_at = 0.0

    def _allow(self) -> bool:
        if self.state == "open":
            if self.clock() - self._opened_at >= self.reset_timeout:
                self.state = "half_open"
                return True
            return False
        return True

    def call(self, fn: Callable[[], Any]) -> Any:
        if not self._allow():
            raise CircuitOpenError("circuit open")
        try:
            result = fn()
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    def _on_success(self) -> None:
        self.failures = 0
        self.state = "closed"

    def _on_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.failure_threshold:
            self.state = "open"
            self._opened_at = self.clock()
