"""AI assistant that authors/edits a whole agent workflow from natural language.

Given the current graph spec + a user request, it asks the local LLM to return the COMPLETE
updated workflow (nodes + edges, including Python `code` steps), then normalizes it (fills
param defaults, lays nodes out left→right) and validates it as a DAG before handing it back.
On any parse/validation failure the current spec is kept unchanged and the reason is surfaced.
"""

from __future__ import annotations

import json
from typing import Any

from app.services import agent_code as ac
from app.services import agent_nodes as an
from app.services.agent_graph import _validate


def _palette_doc() -> str:
    lines = ["Available node types (use the `key` as a node's \"type\"):"]
    for nt in an.NODE_TYPES:
        params = nt.get("params", [])
        if params:
            ps = ", ".join(
                f"{p['key']}"
                + (f" ({'/'.join(p['options'])})" if p.get("options") else "")
                + f"=default {p['default']!r}".replace("\n", " ")[:60]
                for p in params
            )
        else:
            ps = "no params"
        lines.append(f"- {nt['key']} ({nt['label']}, inputs={nt['inputs']}): {ps}")
    return "\n".join(lines)


_SYSTEM = """You are the workflow architect for a financial-terminal agent builder. You edit a \
directed acyclic graph (DAG) of steps that run left-to-right.

A workflow is JSON: {"nodes":[{"id","type","config":{...}}], "edges":[{"source","target"}]}.
- `id` is a short unique string (n1, n2, ...). `type` MUST be one of the node keys below.
- `config` holds that node type's params (omit a param to use its default).
- An edge connects a source node's output to a target node's input. No cycles.

%(palette)s

The `code` (Python step) node runs sandboxed Python you write in its config `code` string:
- read upstream outputs via `inputs[id]` and text via `summaries[id]` (keyed by source node id)
- helpers available: pd, np, math, bars(sym)->OHLCV DataFrame, quote(sym)->dict
- assign `result` (any JSON-able value) and optional `summary` (text)
- NO imports, no dunder access, no eval/exec/open. Keep it short and correct.

RULES:
- Return the COMPLETE updated workflow (every node + edge), not just the change.
- Preserve nodes the user did not ask to change (same id/type/config).
- Keep it a connected DAG that flows from input/data nodes to an output node.
- Reply ONLY with a single JSON object: {"message": "<1-2 sentence summary of what you changed>", \
"nodes":[...], "edges":[...]}. No markdown, no prose outside the JSON."""


def _strip_to_json(raw: str) -> dict | None:
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


def _layout(nodes: list[dict], edges: list[dict]) -> None:
    """Assign tidy left→right positions by topological depth (mutates nodes in place)."""
    incoming = {n["id"]: [e["source"] for e in edges if e["target"] == n["id"]] for n in nodes}
    depth: dict[str, int] = {}

    def d(nid: str, stack: set[str]) -> int:
        if nid in depth:
            return depth[nid]
        if nid in stack or not incoming.get(nid):
            depth[nid] = 0
            return 0
        depth[nid] = 1 + max((d(s, stack | {nid}) for s in incoming[nid] if s != nid), default=-1)
        return depth[nid]

    for n in nodes:
        d(n["id"], set())
    rows: dict[int, int] = {}
    for n in nodes:
        lvl = depth.get(n["id"], 0)
        row = rows.get(lvl, 0)
        rows[lvl] = row + 1
        n["x"] = 30 + lvl * 195
        n["y"] = 40 + row * 115


def _normalize(data: dict) -> dict:
    raw_nodes = data.get("nodes") or []
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for i, rn in enumerate(raw_nodes):
        nid = str(rn.get("id") or f"n{i + 1}")
        typ = rn.get("type")
        if typ not in an._BY_KEY or nid in seen_ids:
            continue
        seen_ids.add(nid)
        cfg = dict(rn.get("config") or {})
        for p in an._BY_KEY[typ].get("params", []):
            cfg.setdefault(p["key"], p["default"])
        nodes.append({"id": nid, "type": typ, "x": rn.get("x", 0), "y": rn.get("y", 0), "config": cfg})

    ids = {n["id"] for n in nodes}
    edges, seen_e = [], set()
    for re_ in data.get("edges") or []:
        s, t = re_.get("source"), re_.get("target")
        if s in ids and t in ids and s != t and (s, t) not in seen_e:
            seen_e.add((s, t))
            edges.append({"source": s, "target": t})
    return {"nodes": nodes, "edges": edges}


_MAX_ATTEMPTS = 3


def _spec_error(new_spec: dict) -> str | None:
    """Return a human-readable reason the spec is unusable, or None if it's valid."""
    try:
        _validate(new_spec["nodes"], new_spec["edges"])
    except ValueError as e:
        return f"the graph is not a valid DAG ({e})"
    for n in new_spec["nodes"]:
        if n["type"] == "code":
            try:
                ac._validate(str(n["config"].get("code", "")))
            except ValueError as e:
                return f"node {n['id']} contains invalid Python ({e})"
    return None


def assist(llm: Any, model: str, spec: dict, message: str) -> dict:
    """Return {ok, spec, message, attempts}. On failure, spec is the unchanged input spec.

    Validates the model's workflow (DAG + every `code` step's Python). On failure it feeds the
    exact error back and retries up to `_MAX_ATTEMPTS` times, so a one-off bad Python step or
    malformed JSON self-corrects instead of bouncing back to the user.
    """
    system = _SYSTEM % {"palette": _palette_doc()}
    base_user = (
        f"Current workflow:\n{json.dumps(spec, ensure_ascii=False)}\n\n"
        f"Request: {message}\n\nReturn the full updated workflow as a single JSON object."
    )
    user = base_user
    last_err = "the model did not return a usable workflow"

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        raw = llm.complete(system, user, model=model, temperature=0.2)
        data = _strip_to_json(raw)
        if isinstance(data, dict) and "nodes" in data:
            new_spec = _normalize(data)
            err = _spec_error(new_spec)
            if err is None:
                _layout(new_spec["nodes"], new_spec["edges"])
                return {
                    "ok": True,
                    "spec": new_spec,
                    "message": str(data.get("message", "Updated the workflow."))[:1000],
                    "attempts": attempt,
                }
            last_err = err
        else:
            last_err = "the response was not a single JSON workflow object"

        # feed the exact failure back for a corrected retry
        user = (
            base_user
            + f"\n\nYour previous answer was REJECTED because {last_err}.\nPrevious answer:\n{raw[:1500]}\n\n"
            "Fix that problem and return the COMPLETE corrected workflow as one JSON object. "
            "If a `code` node was at fault, make sure its Python parses (watch f-string brackets/quotes — "
            "don't use the same quote char inside an f-string expression)."
        )

    return {
        "ok": False,
        "spec": spec,
        "message": f"Couldn't build a valid workflow after {_MAX_ATTEMPTS} tries: {last_err}. Kept the current one — try rephrasing.",
        "attempts": _MAX_ATTEMPTS,
    }
