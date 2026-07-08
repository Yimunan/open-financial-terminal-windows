"""Managed call layer — every outbound API call goes through one policy pipeline:

    cache → retry( rate-limit → circuit-breaker → fn )

``ManagedClient.call`` wraps *any* callable (so it fronts ccxt/httpx/SDK calls alike).
``ManagedHttpClient`` is the httpx-specific convenience built on top, used by the data
providers' HTTP paths and the LLM research client.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from typing import Any

import httpx

from qhfi.api.breaker import CircuitBreaker, CircuitOpenError
from qhfi.api.cache import TTLCache
from qhfi.api.ratelimit import RateLimiter


class ServerError(RuntimeError):
    """5xx response — retryable (the server is at fault, not the request)."""


class ManagedClient:
    def __init__(
        self,
        rate_per_sec: float = 10.0,
        burst: int | None = None,
        max_retries: int = 2,
        backoff_base: float = 0.2,
        cache_ttl: float = 300.0,
        retry_on: tuple[type[BaseException], ...] = (Exception,),
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.limiter = RateLimiter(rate_per_sec, burst, clock, sleep)
        self.breaker = CircuitBreaker(failure_threshold, reset_timeout, clock)
        self.cache = TTLCache(cache_ttl, clock)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.retry_on = retry_on
        self.sleep = sleep

    def call(self, fn: Callable[[], Any], *, cache_key: Hashable | None = None) -> Any:
        if cache_key is not None:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return hit

        def guarded() -> Any:
            self.limiter.acquire()
            return self.breaker.call(fn)

        result = self._retry(guarded)
        if cache_key is not None:
            self.cache.set(cache_key, result)
        return result

    def _retry(self, fn: Callable[[], Any]) -> Any:
        attempt = 0
        while True:
            try:
                return fn()
            except CircuitOpenError:
                raise                       # open circuit → fail fast, never retry
            except self.retry_on:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                self.sleep(self.backoff_base * (2 ** (attempt - 1)))


_HTTP_RETRY: tuple[type[BaseException], ...] = (httpx.TransportError, ServerError)


class ManagedHttpClient:
    """httpx requests run through a ManagedClient. 5xx → ServerError (retried); 4xx →
    HTTPStatusError (propagated, not retried). Pass ``transport`` (httpx.MockTransport) in
    tests to stay offline."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        rate_per_sec: float = 10.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        managed: ManagedClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(headers=headers, transport=transport)
        self.managed = managed or ManagedClient(rate_per_sec=rate_per_sec, retry_on=_HTTP_RETRY)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(self, method: str, path: str, *, cache: bool = False, **kw: Any) -> Any:
        url = self._url(path)
        key: Hashable | None = None
        if cache:
            key = (method, url, repr(kw.get("params")), repr(kw.get("json")))

        def do() -> Any:
            r = self._http.request(method, url, timeout=self.timeout, **kw)
            if r.status_code >= 500:
                raise ServerError(f"{r.status_code} from {url}")
            r.raise_for_status()
            return r.json() if r.content else None

        return self.managed.call(do, cache_key=key)

    def get(self, path: str, *, cache: bool = False, **kw: Any) -> Any:
        return self.request("GET", path, cache=cache, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def close(self) -> None:
        self._http.close()
