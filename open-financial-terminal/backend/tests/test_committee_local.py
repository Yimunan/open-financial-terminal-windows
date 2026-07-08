"""Tests for the local-LLM committee fallback (services.committee_local.deliberate).

Hermetic: the LLMClient is replaced by a tiny in-process ``_FakeLLM`` whose ``.complete(...)``
returns a fixed string and COUNTS calls — so we pin the *structural* deliberation contract
(role routing, the analyst cap, default-analyst injection, per-persona failure resilience, and
the ``{"result": <str>}`` response shape) WITHOUT asserting any LLM text (which is
non-deterministic). No network, no real model, no proxy router — the committee router is an
httpx proxy and is out of scope here.

Run: `cd backend && pytest tests/test_committee_local.py -q`
"""

from __future__ import annotations

import pytest

from app.services import committee_local as cl


# ── Fake LLM: fixed reply, counted calls ─────────────────────────────────────────────
class _FakeLLM:
    """Counts ``.complete`` calls and records each (system, user). Optionally raises on the
    Nth call (1-based) to exercise the single-persona-failure-resilience branch."""

    def __init__(self, reply: str = "ok", raise_on: int | None = None) -> None:
        self.reply = reply
        self.raise_on = raise_on
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, **_kw) -> str:
        self.calls.append((system, user))
        if self.raise_on is not None and len(self.calls) == self.raise_on:
            raise RuntimeError("persona blew up")
        return self.reply

    @property
    def n_calls(self) -> int:
        return len(self.calls)


def _member(role: str, **extra) -> dict:
    m = {"role": role, "goal": f"{role} goal", "backstory": f"{role} backstory"}
    m.update(extra)
    return m


# ── _is_chair role routing ───────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "role, expected",
    [
        ("Committee Chair", True),
        ("CIO", True),
        ("Chief Investment Officer", True),  # "chief" hint
        ("CRO", True),
        ("cio (lowercase)", True),            # case-insensitive
        ("Strategy Analyst", False),
        ("Risk Analyst", False),
        ("", False),
    ],
)
def test_is_chair_matches_role_hints(role, expected):
    assert cl._is_chair({"role": role}) is expected


def test_is_chair_missing_role_is_false():
    assert cl._is_chair({}) is False


# ── deliberate: chair selection + analyst cap ────────────────────────────────────────
def test_deliberate_caps_analysts_and_picks_chair():
    llm = _FakeLLM()
    members = [_member("Committee Chair")] + [_member(f"Analyst {i}") for i in range(6)]
    out = cl.deliberate(llm, "m", "Macro Committee", members, "Is the strategy robust?")

    # 6 non-chair analysts capped to _MAX_ANALYSTS (=4) persona calls + 1 chair synthesis.
    assert cl._MAX_ANALYSTS == 4
    assert llm.n_calls == cl._MAX_ANALYSTS + 1
    # The explicit chair member is used for synthesis, not invented from the committee name.
    chair_system = llm.calls[-1][0]
    assert "Committee Chair" in chair_system
    assert isinstance(out, dict) and isinstance(out["result"], str)


def test_deliberate_caps_analysts_when_no_chair_present():
    # No chair in the roster: every member counts as a non-chair analyst, still capped to 4.
    llm = _FakeLLM()
    members = [_member(f"Analyst {i}") for i in range(6)]
    cl.deliberate(llm, "m", "Equity Committee", members, "mandate")
    assert llm.n_calls == cl._MAX_ANALYSTS + 1  # 4 personas + 1 synthesized chair


# ── deliberate: chair synthesized when none supplied ─────────────────────────────────
def test_deliberate_synthesizes_chair_when_missing():
    llm = _FakeLLM()
    out = cl.deliberate(llm, "m", "Credit Committee", [_member("Risk Analyst")], "mandate")
    # 1 analyst persona + 1 synthesized-chair call.
    assert llm.n_calls == 2
    # The synthesized chair role is "{committee} Chair".
    assert "Credit Committee Chair" in llm.calls[-1][0]
    assert out["result"]


def test_deliberate_injects_default_analyst_for_empty_roster():
    # Empty/None roster → a default "Strategy Analyst" is injected so the panel is never empty.
    for roster in ([], None):
        llm = _FakeLLM()
        out = cl.deliberate(llm, "m", "Strategy Committee", roster, "mandate")
        assert llm.n_calls == 2  # 1 injected analyst persona + 1 synthesized chair
        assert "Strategy Analyst" in llm.calls[0][0]
        assert "Strategy Committee Chair" in llm.calls[-1][0]
        assert out["result"]


def test_deliberate_chair_only_roster_injects_default_analyst():
    # Only a chair, no analysts → default analyst injected; chair is the supplied member.
    llm = _FakeLLM()
    cl.deliberate(llm, "m", "Macro Committee", [_member("CIO")], "mandate")
    assert llm.n_calls == 2  # default analyst + the real chair
    assert "Strategy Analyst" in llm.calls[0][0]
    assert "CIO" in llm.calls[-1][0]


# ── deliberate: one persona failure does NOT sink the panel ──────────────────────────
def test_deliberate_survives_one_persona_failure():
    # Raise on the 2nd call (the 2nd of 3 analyst personas). The panel must still deliver a
    # chair verdict: 3 persona attempts (one raises) + 1 chair synthesis = 4 total calls.
    llm = _FakeLLM(raise_on=2)
    members = [_member("Analyst A"), _member("Analyst B"), _member("Analyst C")]
    out = cl.deliberate(llm, "m", "Committee", members, "mandate")

    assert llm.n_calls == 4  # 3 persona attempts + 1 synthesis (the raise is swallowed)
    assert isinstance(out["result"], str) and out["result"]
    # The failed persona (Analyst B) is dropped from the transcript; the other two remain.
    assert "Analyst A" in out["result"]
    assert "Analyst C" in out["result"]
    assert "Analyst B" not in out["result"]


def test_deliberate_all_personas_fail_still_synthesizes():
    # Every persona raises → no takes, but the chair synthesis call still fires once.
    class _AllRaise(_FakeLLM):
        def complete(self, system, user, **kw):
            self.calls.append((system, user))
            if "chairing the committee" not in system:  # personas raise, chair succeeds
                raise RuntimeError("nope")
            return self.reply

    llm = _AllRaise()
    out = cl.deliberate(llm, "m", "Committee", [_member("A"), _member("B")], "mandate")
    # 2 persona attempts (both raise) + 1 chair synthesis.
    assert llm.n_calls == 3
    # No transcript → result is just the chair synthesis (no "---" separator).
    assert out["result"] == "ok"
    assert "---" not in out["result"]


# ── deliberate: response shape ───────────────────────────────────────────────────────
def test_deliberate_returns_result_shape():
    llm = _FakeLLM(reply="VERDICT")
    out = cl.deliberate(llm, "m", "Committee", [_member("Analyst A")], "mandate")
    assert set(out) == {"result"}
    assert isinstance(out["result"], str)
    # With at least one successful take, the transcript is joined to the verdict by a "---" rule.
    assert "---" in out["result"]
    assert "VERDICT" in out["result"]
