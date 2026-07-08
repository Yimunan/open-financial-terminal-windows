"""Local LLM committee — a dependency-free fallback for the external CrewAI service (:8083).

The CrewAI committee service is a separate, often-offline process. So that the committee node
still produces a *real* multi-perspective review (and the research-refinement workflow keeps its
value), this runs a short deliberation through the local `LLMClient` — the same proxy the rest of
the terminal uses — and synthesizes a Chair verdict in the SAME response shape the CrewAI service
returns (``{"result": <markdown with a fenced ```json verdict block>}``). The committee node and
`_parse_verdict` therefore work unchanged whether the verdict came from CrewAI or from here.

It's deliberately small: one short call per analyst persona (capped), then one Chair synthesis.
Sequential, low-temperature, bounded output — a few seconds total against a warm model.
"""

from __future__ import annotations

import asyncio
from typing import Any

_MAX_ANALYSTS = 4
_CHAIR_HINTS = ("chair", "cio", "chief", "cro")  # roles that synthesize rather than opine


def _is_chair(member: dict) -> bool:
    return any(h in (member.get("role", "").lower()) for h in _CHAIR_HINTS)


def _persona_take(llm: Any, model: str, member: dict, mandate: str) -> str:
    role = member.get("role", "Analyst")
    system = (
        f"You are the {role} on an investment committee. {member.get('goal', '')} "
        f"{member.get('backstory', '')} Give a SHORT (2-3 sentence) opinion on the matter below "
        "in your role's voice — specific and decisive, no preamble."
    )
    return llm.complete(system, mandate, model=model, temperature=0.5).strip()


def _chair_synthesis(llm: Any, model: str, chair: dict, mandate: str, takes: list[tuple[str, str]]) -> str:
    role = chair.get("role", "Chair (CIO)")
    panel = "\n\n".join(f"{r}:\n{t}" for r, t in takes) or "(no analyst opinions were produced)"
    system = (
        f"You are the {role}, chairing the committee. Weigh the members' opinions and issue the "
        "committee's decision. Respond with a 2-3 sentence rationale, THEN a fenced JSON verdict "
        "block EXACTLY like:\n"
        "```json\n"
        '{"recommendation":"deploy|refine|reject","conviction":"high|medium|low",'
        '"sizing":"<short>","key_risks":["<the single most impactful improvement>"],'
        '"dissent":"<minority view or none>"}\n'
        "```\n"
        "key_risks must name the most impactful concrete change to act on next."
    )
    user = f"Matter under review:\n{mandate}\n\nMember opinions:\n{panel}"
    return llm.complete(system, user, model=model, temperature=0.3).strip()


def deliberate(llm: Any, model: str, committee: str, members: list[dict] | None, mandate: str) -> dict:
    """Run a local multi-persona deliberation; return ``{"result": markdown+fenced-json}``.

    Mirrors the CrewAI ``/run`` response so the caller parses it identically. Raises only if the
    LLM is entirely unusable (the node then degrades to 'review unavailable')."""
    roster = list(members or [])
    chair = next((m for m in roster if _is_chair(m)), None)
    analysts = [m for m in roster if m is not chair][:_MAX_ANALYSTS]
    if not analysts:
        analysts = [{"role": "Strategy Analyst", "goal": "Assess the strategy's robustness.", "backstory": ""}]
    if chair is None:
        chair = {"role": f"{committee} Chair"}

    takes: list[tuple[str, str]] = []
    for m in analysts:
        try:
            takes.append((m.get("role", "Analyst"), _persona_take(llm, model, m, mandate)))
        except Exception:  # noqa: BLE001 - a single persona failing shouldn't sink the panel
            continue

    verdict_md = _chair_synthesis(llm, model, chair, mandate, takes)
    transcript = "\n\n".join(f"**{r}**\n{t}" for r, t in takes)
    result = f"{transcript}\n\n---\n\n{verdict_md}" if transcript else verdict_md
    return {"result": result}


def _split_for_stream(committee: str, members: list[dict] | None) -> tuple[list[dict], dict]:
    """Return ``(analysts, chair)`` using the **last** member as Chair.

    This mirrors the Committee widget's convention (``defaultLayout``/``defaultEdges`` treat
    ``members[-1]`` as the synthesizer), so the streamed ``task`` frames line up with the exact
    agent cards the user sees — unlike ``deliberate`` above, which hint-matches the Chair for the
    non-visual agent-node path.
    """
    roster = [m for m in (members or []) if (m.get("role") or "").strip()]
    if not roster:
        return ([{"role": "Strategy Analyst", "goal": "Assess the matter under review.", "backstory": ""}],
                {"role": f"{committee} Chair"})
    if len(roster) == 1:
        return ([], roster[0])
    return (roster[:-1], roster[-1])


async def stream_deliberation(llm: Any, model: str, committee: str, members: list[dict] | None,
                              mandate: str):
    """Async-yield ``(event, payload)`` frames mirroring the crewai-service SSE stream, so the
    Committee widget renders a live *local* deliberation when the external service is unavailable.

    Emits ``("task", {"agent": role, "raw": text})`` per analyst as each opinion lands, then
    ``("result", <chair markdown+fenced-json>)`` — the same shape the router relays from the real
    service, so the widget needs no changes. Blocking LLM calls run in a worker thread so the
    WebSocket stays responsive. Raises only if the Chair synthesis itself fails (the caller then
    surfaces an error frame); a single analyst failing is skipped, not fatal.
    """
    analysts, chair = _split_for_stream(committee, members)
    takes: list[tuple[str, str]] = []
    for m in analysts:
        try:
            take = await asyncio.to_thread(_persona_take, llm, model, m, mandate)
        except Exception:  # noqa: BLE001 - one persona failing shouldn't sink the panel
            continue
        takes.append((m.get("role", "Analyst"), take))
        yield "task", {"agent": m.get("role", "Analyst"), "raw": take}
    verdict_md = await asyncio.to_thread(_chair_synthesis, llm, model, chair, mandate, takes)
    yield "result", verdict_md
