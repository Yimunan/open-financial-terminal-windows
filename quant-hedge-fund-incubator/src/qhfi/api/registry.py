"""Endpoint + credential registry — one place that knows every external API the framework
talks to, its base URL, key, and rate budget. Built from Settings (.env), so credentials and
URLs live in config, not scattered through call sites.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from qhfi.api.client import ManagedHttpClient
from qhfi.core.config import Settings, get_settings


@dataclass
class Endpoint:
    name: str
    base_url: str
    api_key: str | None = None
    rate_per_sec: float = 10.0
    timeout: float = 30.0


class ApiRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self._endpoints: dict[str, Endpoint] = {
            # Local LLM stack (see reference memories): proxy / LangGraph / crewAI
            "llm": Endpoint("llm", s.llm_base_url, s.llm_api_key, rate_per_sec=5.0, timeout=120.0),
            "langgraph": Endpoint("langgraph", s.langgraph_url, rate_per_sec=5.0, timeout=120.0),
            "crewai": Endpoint("crewai", s.crewai_url, rate_per_sec=5.0, timeout=180.0),
        }

    def register(self, endpoint: Endpoint) -> None:
        self._endpoints[endpoint.name] = endpoint

    def get(self, name: str) -> Endpoint:
        try:
            return self._endpoints[name]
        except KeyError:
            raise LookupError(f"unknown endpoint {name!r}; known: {sorted(self._endpoints)}") from None

    def names(self) -> list[str]:
        return sorted(self._endpoints)

    def client(self, name: str, transport: httpx.BaseTransport | None = None) -> ManagedHttpClient:
        ep = self.get(name)
        return ManagedHttpClient(
            ep.base_url, api_key=ep.api_key, rate_per_sec=ep.rate_per_sec,
            timeout=ep.timeout, transport=transport,
        )
