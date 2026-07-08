"""Shared helper to pull one JSON object out of a (possibly fenced / chatty) LLM reply.

Several agents (agent_assistant, agent_coder, backtest_agent, …) carry their own copy of this; new
code imports it from here. Defensive: handles ```json fences and first-brace/last-brace extraction,
returns None on failure rather than raising.
"""

from __future__ import annotations

import json


def strip_to_json(raw: str) -> dict | None:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(s[a : b + 1])
        except json.JSONDecodeError:
            return None
    return None
