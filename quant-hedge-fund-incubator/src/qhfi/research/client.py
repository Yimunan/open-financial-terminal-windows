"""Clients for the local LLM stack.

The framework is a *client* of your existing services — it does not embed LangGraph/crewAI:

  * ``LLMClient``       → OpenAI-compatible vLLM auto-swap proxy (:8001) for single-shot
                          completions / structured output (ideation, critique).
  * ``LangGraphBridge`` → POST to the LangGraph FastAPI service (:8082) for multi-step graphs.
  * ``CrewAIBridge``    → POST to the crewAI FastAPI service (:8083) for crew decomposition.

All use httpx. Structured output is requested via the proxy's OpenAI-compatible
``response_format`` (json schema) so agents return validated objects, not prose.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from qhfi.api.client import ManagedHttpClient
from qhfi.api.registry import ApiRegistry
from qhfi.core.config import Settings, get_settings


def _parse_json(text: str) -> dict:
    """json.loads with markdown code fences stripped — some models fence even 'JSON only' output."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t.lstrip("`")
        t = t.rsplit("```", 1)[0].strip()
    return json.loads(t)


class LLMClient:
    """Calls the OpenAI-compatible vLLM proxy through the managed API layer (rate-limit,
    retry/backoff, circuit breaker). Inject ``http`` (a ManagedHttpClient over an httpx
    MockTransport) in tests to stay offline."""

    def __init__(self, settings: Settings | None = None, http: ManagedHttpClient | None = None) -> None:
        self.s = settings or get_settings()
        self.http = http or ApiRegistry(self.s).client("llm")
        # None = unprobed; set once from the first structured() call. Remembering the answer
        # matters: a 4xx counts as a circuit-breaker failure, so re-probing an unsupported
        # provider every call would open the breaker after failure_threshold calls.
        self._json_schema_ok: bool | None = None

    def complete(self, system: str, user: str, model: str | None = None, **kw: Any) -> str:
        """Single chat completion against the proxy → assistant text.

        Note: per memory, Qwen3.6 reasoning is default-off at the proxy and gemma needs its
        reasoning/tool parsers paired — model choice is the proxy's concern, not ours.
        """
        payload = {
            "model": model or self.s.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kw,
        }
        data = self.http.post("/chat/completions", json=payload)
        return data["choices"][0]["message"]["content"]

    def structured(self, system: str, user: str, schema: dict, model: str | None = None) -> dict:
        """Completion constrained to a JSON schema → parsed dict.

        Providers implementing OpenAI's ``json_schema`` response_format (vLLM proxy) get true
        constrained decoding. Providers that 400 on it (DeepSeek: "This response_format type is
        unavailable now") fall back to their ``json_object`` mode with the schema embedded in the
        system prompt; the capability is remembered per client so only the first call pays the
        probe. Non-400 errors (auth, rate limit, 5xx) propagate unchanged.
        """
        mdl = model or self.s.llm_model
        if self._json_schema_ok is not False:
            payload = {
                "model": mdl,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}},
            }
            try:
                data = self.http.post("/chat/completions", json=payload)
                self._json_schema_ok = True
                return _parse_json(data["choices"][0]["message"]["content"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 400:
                    raise
                self._json_schema_ok = False
        # json_object fallback: schema rides in the prompt instead of the decoder. "JSON" must
        # appear in the messages or DeepSeek/OpenAI reject json_object mode outright.
        payload = {
            "model": mdl,
            "messages": [
                {"role": "system", "content": (
                    f"{system}\n\nRespond with a single JSON object — no prose, no code fences — "
                    f"that validates against this JSON Schema:\n{json.dumps(schema)}"
                )},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        data = self.http.post("/chat/completions", json=payload)
        return _parse_json(data["choices"][0]["message"]["content"])


class LangGraphBridge:
    def __init__(self, settings: Settings | None = None) -> None:
        self.url = (settings or get_settings()).langgraph_url

    def invoke(self, graph: str, payload: dict) -> dict:
        raise NotImplementedError("TODO: POST {url}/invoke/{graph} → result")


class CrewAIBridge:
    def __init__(self, settings: Settings | None = None) -> None:
        self.url = (settings or get_settings()).crewai_url

    def kickoff(self, crew: str, inputs: dict) -> dict:
        raise NotImplementedError("TODO: POST {url}/kickoff/{crew} → result")
