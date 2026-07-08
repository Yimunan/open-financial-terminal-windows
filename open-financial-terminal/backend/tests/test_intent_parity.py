"""Contract parity: the backend intent vocabulary (app/schemas/intents.py) must stay in lockstep
with the frontend's hand-written TypeScript copy (frontend/src/state/intents.ts + linking.ts).

The two are hand-synced (like every other Pydantic↔TS contract in this repo); this test is the
tripwire that turns silent drift into a red build. It parses the TS source textually — no node/tsc
needed — and skips cleanly if the frontend tree isn't present (e.g. a backend-only checkout).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas import intents as I
from app.services import assistant_agent as agent

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INTENTS_TS = _REPO_ROOT / "frontend" / "src" / "state" / "intents.ts"
_LINKING_TS = _REPO_ROOT / "frontend" / "src" / "state" / "linking.ts"

pytestmark = pytest.mark.skipif(
    not _INTENTS_TS.exists() or not _LINKING_TS.exists(),
    reason="frontend source not present — parity check only runs in a full checkout",
)


def _slice(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start + len(start_marker))
    return text[start:end]


def _kind_literals(block: str) -> set[str]:
    return set(re.findall(r'kind:\s*"([a-z_]+)"', block))


def test_intent_kinds_match() -> None:
    ts = _INTENTS_TS.read_text(encoding="utf-8")

    # The `Intent` discriminated union's `kind` literals.
    union = _slice(ts, "export type Intent =", "export type IntentKind")
    ts_union_kinds = _kind_literals(union)

    # The hand-maintained INTENT_KINDS runtime array (must match its own union).
    arr_block = _slice(ts, "INTENT_KINDS: IntentKind[] = [", "];")
    ts_array_kinds = set(re.findall(r'"([a-z_]+)"', arr_block))

    assert ts_union_kinds == ts_array_kinds, "TS Intent union and INTENT_KINDS array disagree"
    assert ts_union_kinds == set(I.INTENT_KINDS), (
        f"Intent kinds drifted: TS={sorted(ts_union_kinds)} backend={sorted(I.INTENT_KINDS)}"
    )


def test_send_payload_kinds_match() -> None:
    ts = _INTENTS_TS.read_text(encoding="utf-8")
    block = _slice(ts, "export type SendPayload =", "export type SendPayloadKind")
    ts_kinds = _kind_literals(block)
    assert ts_kinds == set(I.SEND_PAYLOAD_KINDS), (
        f"SendPayload kinds drifted: TS={sorted(ts_kinds)} backend={sorted(I.SEND_PAYLOAD_KINDS)}"
    )


def test_channel_context_fields_match() -> None:
    ts = _LINKING_TS.read_text(encoding="utf-8")
    # Slice to the NEXT declaration, not the first `}` — ChannelContext has a nested object literal
    # (`range?: { start; end }`) whose brace would truncate the body early.
    symbol_ref = _slice(ts, "export interface SymbolRef {", "export interface ChannelContext")
    channel_ctx = _slice(ts, "export interface ChannelContext extends SymbolRef {", "interface LinkingState")

    def fields(block: str) -> set[str]:
        out: set[str] = set()
        for line in block.splitlines():
            m = re.match(r"\s*([A-Za-z_]\w*)\??\s*:", line)
            if m:
                out.add(m.group(1))
        return out

    ts_fields = fields(symbol_ref) | fields(channel_ctx)
    backend_fields = set(I.ChannelContext.model_fields.keys())
    assert ts_fields == backend_fields, (
        f"ChannelContext fields drifted: TS={sorted(ts_fields)} backend={sorted(backend_fields)}"
    )


def test_client_actions_map_to_intents() -> None:
    # Every legacy Assistant verb maps to a known intent kind, and the mapping covers exactly the
    # agent's CLIENT_ACTIONS allowlist.
    assert set(I.CLIENT_ACTION_TO_INTENT) == set(agent.CLIENT_ACTIONS)
    assert set(I.CLIENT_ACTION_TO_INTENT.values()) == set(I.CLIENT_INTENT_KINDS)
    assert set(I.CLIENT_INTENT_KINDS) <= set(I.INTENT_KINDS)
