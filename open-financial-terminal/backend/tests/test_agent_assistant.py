"""Tests for the NL→whole-workflow author (services.agent_assistant).

Hermetic: the LLM is a scripted stub (`_ScriptLLM`) whose `.complete(...)` pops canned raw
strings off a list, so no network/model is ever touched. The substance under test is the pure
plumbing around the model call — the fence-tolerant JSON extractor (`_strip_to_json`), the
spec normalizer (drop unknown node types / dup ids, fill param defaults, filter edges), the
left→right topological `_layout`, and the `assist` retry loop (validate the DAG + every `code`
step's Python, feed the error back, retry up to `_MAX_ATTEMPTS`, keep the input spec on
exhaustion).

Real node-type keys are pulled live from `agent_nodes._BY_KEY` so `_normalize`/`_spec_error`
accept them: `input`/`output` as generic nodes and `code` as the Python step.

Run: `cd backend && pytest tests/test_agent_assistant.py -v`
"""

from __future__ import annotations

import json

from app.services import agent_assistant as aa
from app.services import agent_nodes as an


# ── scripted LLM (no network) ───────────────────────────────────────────────────────
class _ScriptLLM:
    """`.complete(system, user, ...)` pops the next canned raw reply off `replies`."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, **_kw) -> str:
        self.calls.append((system, user))
        return self.replies.pop(0)


# ── workflow builders using REAL node-type keys ─────────────────────────────────────
# Sanity: the keys we lean on really exist in the live palette.
assert "input" in an._BY_KEY and "output" in an._BY_KEY and "code" in an._BY_KEY


def _good_workflow_json(code: str = "result = 1\nsummary = 'ok'") -> str:
    """A valid input→code→output DAG as the model's raw JSON reply."""
    return json.dumps({
        "message": "Built a tiny pipeline.",
        "nodes": [
            {"id": "n1", "type": "input", "config": {"symbol": "MSFT"}},
            {"id": "n2", "type": "code", "config": {"code": code}},
            {"id": "n3", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    })


# ── _strip_to_json ──────────────────────────────────────────────────────────────────
def test_strip_to_json_handles_fences_and_slop():
    # bare JSON
    assert aa._strip_to_json('{"a": 1}') == {"a": 1}

    # ```json fenced
    fenced = '```json\n{"a": 2}\n```'
    assert aa._strip_to_json(fenced) == {"a": 2}

    # plain ``` fence (no language tag)
    plain_fence = '```\n{"a": 3}\n```'
    assert aa._strip_to_json(plain_fence) == {"a": 3}

    # prose around the object → recovered by first-brace / last-brace slice
    slop = 'Sure! Here is the workflow:\n{"a": 4, "b": [1, 2]}\nHope that helps.'
    assert aa._strip_to_json(slop) == {"a": 4, "b": [1, 2]}

    # unrecoverable garbage → None
    assert aa._strip_to_json("not json at all, no braces") is None
    assert aa._strip_to_json("{ definitely : not, valid }") is None


# ── _normalize ──────────────────────────────────────────────────────────────────────
def test_normalize_drops_unknown_types_and_dup_ids():
    data = {
        "nodes": [
            {"id": "n1", "type": "input", "config": {}},
            {"id": "n2", "type": "does_not_exist", "config": {}},  # unknown type → dropped
            {"id": "n1", "type": "output", "config": {}},          # dup id → dropped
            {"id": "n3", "type": "output", "config": {}},
        ],
        "edges": [],
    }
    out = aa._normalize(data)
    ids = [n["id"] for n in out["nodes"]]
    types = {n["id"]: n["type"] for n in out["nodes"]}
    assert ids == ["n1", "n3"]              # unknown + dup removed, order preserved
    assert types["n1"] == "input"           # the FIRST n1 wins (later dup discarded)
    assert types["n3"] == "output"


def test_normalize_fills_param_defaults_and_filters_edges():
    data = {
        "nodes": [
            {"id": "n1", "type": "input", "config": {"symbol": "TSLA"}},  # asset omitted
            {"id": "n2", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},   # valid
            {"source": "n1", "target": "n2"},   # duplicate → dropped
            {"source": "n2", "target": "n2"},   # self-loop → dropped
            {"source": "n1", "target": "ghost"},  # missing endpoint → dropped
            {"source": "ghost", "target": "n2"},  # missing endpoint → dropped
        ],
    }
    out = aa._normalize(data)

    n1 = next(n for n in out["nodes"] if n["id"] == "n1")
    # explicit config kept, missing param filled from _BY_KEY defaults
    assert n1["config"]["symbol"] == "TSLA"
    assert n1["config"]["asset"] == an._BY_KEY["input"]["params"][1]["default"]  # "equity"

    assert out["edges"] == [{"source": "n1", "target": "n2"}]  # only the one valid, deduped edge


# ── _layout ─────────────────────────────────────────────────────────────────────────
def test_layout_assigns_increasing_x_by_depth():
    nodes = [
        {"id": "n1", "type": "input", "config": {}},
        {"id": "n2", "type": "code", "config": {}},
        {"id": "n3", "type": "output", "config": {}},
    ]
    edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n3"}]
    aa._layout(nodes, edges)
    by_id = {n["id"]: n for n in nodes}
    # x strictly increases by topological depth (col 0 → +195 per level)
    assert by_id["n1"]["x"] < by_id["n2"]["x"] < by_id["n3"]["x"]
    assert by_id["n1"]["x"] == 30
    assert by_id["n2"]["x"] == 30 + 195
    assert by_id["n3"]["x"] == 30 + 2 * 195
    # all nodes received a y coordinate
    assert all("y" in n for n in nodes)


def test_layout_tolerates_cycle_without_infinite_recursion():
    # a cyclic incoming map must not blow the stack (the depth walker guards with `stack`)
    nodes = [
        {"id": "n1", "type": "input", "config": {}},
        {"id": "n2", "type": "output", "config": {}},
    ]
    edges = [{"source": "n1", "target": "n2"}, {"source": "n2", "target": "n1"}]
    aa._layout(nodes, edges)  # returns (does not recurse forever)
    assert all("x" in n and "y" in n for n in nodes)


# ── assist (retry loop) ─────────────────────────────────────────────────────────────
def test_assist_returns_spec_on_first_valid_reply():
    llm = _ScriptLLM([_good_workflow_json()])
    spec = {"nodes": [], "edges": []}
    res = aa.assist(llm, "m", spec, "build a tiny pipeline")

    assert res["ok"] is True
    assert res["attempts"] == 1
    assert len(llm.calls) == 1
    assert res["message"] == "Built a tiny pipeline."
    out_ids = [n["id"] for n in res["spec"]["nodes"]]
    assert out_ids == ["n1", "n2", "n3"]
    assert res["spec"]["edges"] == [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
    ]
    # layout ran on the returned spec
    assert all("x" in n and "y" in n for n in res["spec"]["nodes"])


def test_assist_retries_then_succeeds_on_bad_code_node():
    # first reply: a `code` node whose Python is invalid (banned import) → rejected, error fed back.
    bad = _good_workflow_json(code="import os\nresult = 1")
    good = _good_workflow_json(code="result = 42\nsummary = 'fixed'")
    llm = _ScriptLLM([bad, good])
    res = aa.assist(llm, "m", {"nodes": [], "edges": []}, "give me a code step")

    assert res["ok"] is True
    assert res["attempts"] == 2
    assert len(llm.calls) == 2
    # the retry prompt carries the exact rejection reason (a code-node Python failure)
    retry_user = llm.calls[1][1]
    assert "REJECTED" in retry_user
    assert "invalid Python" in retry_user
    # the surviving code node is the corrected one
    code_node = next(n for n in res["spec"]["nodes"] if n["type"] == "code")
    assert "result = 42" in code_node["config"]["code"]


def test_assist_retries_on_bad_dag_then_succeeds():
    # first reply: an empty workflow — _validate raises "graph is empty" (a DAG error), retried.
    empty = json.dumps({"message": "empty", "nodes": [], "edges": []})
    llm = _ScriptLLM([empty, _good_workflow_json()])
    res = aa.assist(llm, "m", {"nodes": [], "edges": []}, "anything")

    assert res["ok"] is True
    assert res["attempts"] == 2
    assert "not a valid DAG" in llm.calls[1][1]


def test_assist_keeps_input_spec_when_all_attempts_fail():
    input_spec = {
        "nodes": [{"id": "keep", "type": "output", "x": 7, "y": 9, "config": {}}],
        "edges": [],
    }
    # every reply is non-JSON garbage → never validates → exhausts _MAX_ATTEMPTS
    llm = _ScriptLLM(["nope"] * aa._MAX_ATTEMPTS)
    res = aa.assist(llm, "m", input_spec, "do something impossible")

    assert res["ok"] is False
    assert res["attempts"] == aa._MAX_ATTEMPTS
    assert len(llm.calls) == aa._MAX_ATTEMPTS
    assert res["spec"] is input_spec       # the untouched input spec is handed back
    assert "Kept the current one" in res["message"]
