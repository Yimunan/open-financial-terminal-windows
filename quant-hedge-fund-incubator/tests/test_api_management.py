"""Tests for the outbound API management layer: rate limiter, circuit breaker, TTL cache,
the composed ManagedClient (retry/cache/fail-fast), the httpx ManagedHttpClient (via
MockTransport), the registry, and the LLMClient wired on top. Offline + deterministic.
"""

from __future__ import annotations

import json

import httpx
import pytest

from qhfi.api.breaker import CircuitBreaker, CircuitOpenError
from qhfi.api.cache import TTLCache
from qhfi.api.client import _HTTP_RETRY, ManagedClient, ManagedHttpClient, ServerError
from qhfi.api.ratelimit import RateLimiter
from qhfi.api.registry import ApiRegistry
from qhfi.research.client import LLMClient


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


# ── primitives ───────────────────────────────────────────────────────────────
def test_rate_limiter_sleeps_only_when_empty():
    fc = FakeClock()
    rl = RateLimiter(rate=10.0, burst=2, clock=fc, sleep=fc.sleep)
    assert rl.acquire() == 0.0 and rl.acquire() == 0.0   # burst of 2 → immediate
    waited = rl.acquire()                                 # empty → wait ~1/10s
    assert waited == pytest.approx(0.1, rel=1e-6)
    assert fc.t == pytest.approx(0.1, rel=1e-6)


def test_circuit_breaker_opens_then_recovers():
    fc = FakeClock()
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=5.0, clock=fc)
    boom = lambda: (_ for _ in ()).throw(ValueError("x"))  # noqa: E731
    for _ in range(2):
        with pytest.raises(ValueError):
            cb.call(boom)
    assert cb.state == "open"

    calls = []
    with pytest.raises(CircuitOpenError):          # rejected, fn never runs
        cb.call(lambda: calls.append(1))
    assert calls == []

    fc.t += 6.0                                    # past reset_timeout → half-open trial
    assert cb.call(lambda: "ok") == "ok"
    assert cb.state == "closed"


def test_ttl_cache_expires():
    fc = FakeClock()
    c = TTLCache(ttl=10.0, clock=fc)
    c.set("k", 42)
    assert c.get("k") == 42
    fc.t += 11.0
    assert c.get("k") is None


# ── ManagedClient ────────────────────────────────────────────────────────────
def test_managed_retries_then_succeeds():
    fc = FakeClock()
    mc = ManagedClient(retry_on=(ValueError,), max_retries=2, backoff_base=0.1, clock=fc, sleep=fc.sleep)
    n = {"i": 0}

    def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise ValueError("transient")
        return "ok"

    assert mc.call(flaky) == "ok"
    assert n["i"] == 3 and fc.t > 0   # backed off between attempts


def test_managed_gives_up_after_max_retries():
    mc = ManagedClient(retry_on=(ValueError,), max_retries=2, backoff_base=0.0)
    n = {"i": 0}

    def always():
        n["i"] += 1
        raise ValueError("nope")

    with pytest.raises(ValueError):
        mc.call(always)
    assert n["i"] == 3                 # 1 try + 2 retries


def test_managed_cache_avoids_recompute():
    mc = ManagedClient(backoff_base=0.0)
    n = {"i": 0}

    def fn():
        n["i"] += 1
        return n["i"]

    assert mc.call(fn, cache_key="k") == 1
    assert mc.call(fn, cache_key="k") == 1   # served from cache, fn not re-run
    assert n["i"] == 1


# ── ManagedHttpClient (offline via MockTransport) ──────────────────────────────
def _client(handler, **mc_kw):
    managed = ManagedClient(retry_on=(ServerError, httpx.TransportError), backoff_base=0.0, **mc_kw)
    return ManagedHttpClient("http://x/v1", transport=httpx.MockTransport(handler), managed=managed)


def test_http_happy_path():
    c = _client(lambda r: httpx.Response(200, json={"ok": True}))
    assert c.get("/ping") == {"ok": True}


def test_http_retries_5xx_then_succeeds():
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        return httpx.Response(503 if state["n"] == 1 else 200, json={"n": state["n"]})

    assert _client(handler).get("/x") == {"n": 2}
    assert state["n"] == 2


def test_http_does_not_retry_4xx():
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        return httpx.Response(404, json={"err": "nope"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).get("/missing")
    assert state["n"] == 1           # client error → no retry


# ── registry + LLMClient ───────────────────────────────────────────────────────
def test_registry_has_llm_stack_and_rejects_unknown():
    reg = ApiRegistry()
    assert {"llm", "langgraph", "crewai"} <= set(reg.names())
    with pytest.raises(LookupError):
        reg.get("nope")


def test_llmclient_complete_and_structured_via_mock():
    def handler(request):
        body = httpx.Response  # noqa: F841
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"title": "hi"}'}}]
        })

    http = ManagedHttpClient("http://x/v1", transport=httpx.MockTransport(handler),
                             managed=ManagedClient(backoff_base=0.0))
    client = LLMClient(http=http)
    assert client.complete("sys", "user") == '{"title": "hi"}'
    assert client.structured("sys", "user", schema={"type": "object"}) == {"title": "hi"}


def test_llmclient_structured_falls_back_to_json_object():
    """Providers that 400 on json_schema (DeepSeek) → json_object fallback, probed exactly once.

    The probe must be remembered per client: every 4xx counts as a circuit-breaker failure, so
    re-probing on each call would open the breaker after failure_threshold structured() calls.
    """
    state = {"schema": 0, "object": 0}

    def handler(request):
        body = json.loads(request.content)
        if (body.get("response_format") or {}).get("type") == "json_schema":
            state["schema"] += 1
            return httpx.Response(400, json={
                "error": {"message": "This response_format type is unavailable now"}})
        state["object"] += 1
        assert "JSON Schema" in body["messages"][0]["content"]  # schema moved into the prompt
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title": "fb"}'}}]})

    http = ManagedHttpClient("http://x/v1", transport=httpx.MockTransport(handler),
                             managed=ManagedClient(backoff_base=0.0, retry_on=_HTTP_RETRY))
    client = LLMClient(http=http)
    for _ in range(3):
        assert client.structured("sys", "user", schema={"type": "object"}) == {"title": "fb"}
    assert state == {"schema": 1, "object": 3}   # one probe total, never re-probed


def test_llmclient_structured_propagates_non_400():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    http = ManagedHttpClient("http://x/v1", transport=httpx.MockTransport(handler),
                             managed=ManagedClient(backoff_base=0.0, retry_on=_HTTP_RETRY))
    with pytest.raises(httpx.HTTPStatusError):
        LLMClient(http=http).structured("sys", "user", schema={"type": "object"})
