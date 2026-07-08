"""Outbound API management — managed clients for every external service the framework calls.

Pipeline per call: cache → retry(rate-limit → circuit-breaker → fn). See client.ManagedClient.
"""

from qhfi.api.breaker import CircuitBreaker, CircuitOpenError
from qhfi.api.cache import TTLCache
from qhfi.api.client import ManagedClient, ManagedHttpClient, ServerError
from qhfi.api.ratelimit import RateLimiter
from qhfi.api.registry import ApiRegistry, Endpoint

__all__ = [
    "ApiRegistry", "Endpoint", "ManagedClient", "ManagedHttpClient",
    "RateLimiter", "CircuitBreaker", "CircuitOpenError", "TTLCache", "ServerError",
]
