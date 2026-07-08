"""LLM assistant service.

* ``nl_to_screen`` — translate a natural-language request into screener params
  (schema-constrained, via qhfi's LLMClient.structured).
* ``summarize``    — one-shot summary of a symbol's price action / news (LLMClient.complete).
* ``stream_chat``  — token streaming. qhfi's LLMClient is non-streaming, so this opens a
  direct SSE connection to the same vLLM proxy, reading the endpoint/model from qhfi config.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from qhfi.research.client import LLMClient

from app.services import factors as fac
from app.services.universe import list_universes


def nl_to_screen(llm: LLMClient, query: str, model: str) -> dict:
    """Map a free-text request to {factor, universe, limit, rationale}, validated against the
    actually-available factors and universes."""
    universes = list_universes() or ["dow30"]
    factor_keys = list(fac.CATALOG)
    schema = {
        "type": "object",
        "properties": {
            "factor": {"type": "string", "enum": factor_keys},
            "universe": {"type": "string"},
            "limit": {"type": "integer"},
            "rationale": {"type": "string"},
        },
        "required": ["factor", "universe"],
    }
    factor_desc = ", ".join(f"{k} ({v['label']})" for k, v in fac.CATALOG.items())
    system = (
        "You translate a user's market-screening request into structured screener parameters. "
        f"factor is one of: {factor_desc}. Choose a universe from this list only: "
        f"{', '.join(universes)}. Default limit 25. Give a one-sentence rationale."
    )
    out = llm.structured(system, query, schema, model=model)
    if out.get("factor") not in factor_keys:
        out["factor"] = "momentum"
    if out.get("universe") not in universes:
        out["universe"] = "nasdaq100" if "nasdaq100" in universes else universes[0]
    out.setdefault("limit", 25)
    return out


def summarize(llm: LLMClient, symbol: str, context: str, model: str) -> str:
    system = (
        "You are a concise equity research assistant. Given recent context for a stock, write "
        "a tight 3-4 sentence briefing: trend, notable drivers, and one risk. No preamble, no "
        "disclaimers. This is for an internal terminal, not investment advice."
    )
    user = f"Symbol: {symbol}\n\nContext:\n{context}"
    return llm.complete(system, user, model=model)


async def stream_chat(
    base_url: str, model: str, messages: list[dict], temperature: float = 0.4, api_key: str | None = None
) -> AsyncIterator[str]:
    """Yield assistant token deltas from the proxy/provider via OpenAI-style SSE streaming."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True, "temperature": temperature}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "not-needed" else {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
