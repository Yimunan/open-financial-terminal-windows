"""Agentic workflow coder: an LLM that EDITS a workflow with tools, RUNS it, reads the
node outputs/errors, and iterates until it executes clean.

Unlike `agent_assistant` (one shot: NL -> whole new spec), this is a bounded ReAct loop. Each
turn the model emits ONE JSON action (add_node / edit_config / connect / disconnect /
delete_node / run / done); we execute it against an in-memory spec, stream the action + its
observation + the updated spec to the UI, append the observation to the transcript, and ask
for the next action. `run` executes the real LangGraph and feeds per-node status/errors back so
the agent can self-correct. Bounded by `_MAX_STEPS` / `_MAX_RUNS`.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
from typing import AsyncIterator

from app.services import agent_code as ac
from app.services import agent_graph as ag
from app.services.agent_assistant import _layout, _palette_doc, _strip_to_json
from app.services.agent_nodes import AgentDeps, _BY_KEY

_MAX_STEPS = 26
_MAX_RUNS = 4

_TOOLS_DOC = """Call ONE tool per turn as a single JSON object:
{"thought": "<one short sentence>", "tool": "<name>", "args": {...}}
Finish with: {"thought": "...", "done": true, "message": "<what you built>"}

Tools:
- add_node   {"type": "<node key>", "config": {<params>}}   # appends a node; its id is reported back
- edit_config{"id": "<node id>", "key": "<param>", "value": <value>}   # for a Python step use key "code"
- connect    {"source": "<id>", "target": "<id>"}           # add an edge; the graph must stay acyclic
- disconnect {"source": "<id>", "target": "<id>"}
- delete_node{"id": "<id>"}
- run        {}                                             # execute the whole workflow and observe node outputs/errors
- done       {"message": "..."}

Workflow = nodes (each {id, type, config}) wired by edges into a DAG that flows left->right from
input/data nodes to an output node. Build what the user asked, then `run` to verify every node
finishes without error, fix anything that errors, and `done`. Prefer the pipeline nodes
(data -> strategy -> portfolio -> backtest/execution) for quant tasks; use a Python step (code)
for custom logic.

REUSE the nodes already present — edit or rewire them rather than deleting and rebuilding. Only
delete a node that has no place in the final workflow. Always `run` to verify before `done`."""

_CODE_DOC = (
    "A `code` (Python step) node runs sandboxed Python in its config `code`: read upstream via "
    "inputs[id]/summaries[id], use pd/np/math/bars(sym)/quote(sym), assign `result` and optional "
    "`summary`. No imports, no dunders, no eval/exec/open."
)


def _system() -> str:
    return (
        "You are an autonomous workflow engineer for a financial terminal. You edit a node graph "
        "by issuing tool calls, run it, and fix errors until it works.\n\n"
        + _palette_doc()
        + "\n\n"
        + _CODE_DOC
        + "\n\n"
        + _TOOLS_DOC
        + "\n\nReply with ONLY the JSON action object, nothing else."
    )


def _next_id(spec: dict) -> str:
    mx = 0
    for n in spec["nodes"]:
        m = re.match(r"n(\d+)$", str(n.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"n{mx + 1}"


def _build_user(goal: str, spec: dict, history: list[tuple[str, str]]) -> str:
    lines = [f"User goal: {goal}", "", "Current workflow JSON:", json.dumps(spec, ensure_ascii=False), ""]
    if history:
        lines.append("Your actions so far and what happened:")
        for act, obs in history[-12:]:
            lines.append(f"ACTION: {act}")
            lines.append(f"OBSERVATION: {obs}")
        lines.append("")
    lines.append("Respond with the next action as a single JSON object.")
    return "\n".join(lines)


def _apply(tool: str, args: dict, spec: dict) -> tuple[str, bool]:
    """Apply a non-run mutating tool. Returns (observation, mutated)."""
    nodes, edges = spec["nodes"], spec["edges"]
    ids = {n["id"] for n in nodes}

    if tool == "add_node":
        typ = args.get("type")
        if typ not in _BY_KEY:
            return f"unknown node type '{typ}'. Valid: {', '.join(_BY_KEY)}", False
        nid = _next_id(spec)
        cfg = dict(args.get("config") or {})
        for p in _BY_KEY[typ].get("params", []):
            cfg.setdefault(p["key"], p["default"])
        note = ""
        if typ == "code":
            try:
                ac._validate(str(cfg.get("code", "")))
            except ValueError as e:
                note = f" (warning: {e})"
        nodes.append({"id": nid, "type": typ, "x": 0, "y": 0, "config": cfg})
        return f"added {typ} node as {nid}{note}", True

    if tool == "edit_config":
        nid = args.get("id")
        node = next((n for n in nodes if n["id"] == nid), None)
        if node is None:
            return f"no node '{nid}'", False
        key, val = args.get("key"), args.get("value")
        node.setdefault("config", {})[key] = val
        if node["type"] == "code" and key == "code":
            try:
                ac._validate(str(val))
            except ValueError as e:
                return f"set {nid}.code (warning: {e} — fix before running)", True
        return f"set {nid}.{key}", True

    if tool in ("connect", "disconnect"):
        s, t = args.get("source"), args.get("target")
        if s not in ids or t not in ids:
            return f"edge references a missing node ({s}->{t})", False
        if tool == "disconnect":
            spec["edges"] = [e for e in edges if not (e["source"] == s and e["target"] == t)]
            return f"disconnected {s}->{t}", True
        if any(e["source"] == s and e["target"] == t for e in edges):
            return f"{s}->{t} already exists", False
        trial = edges + [{"source": s, "target": t}]
        try:
            ag._validate(nodes, trial)
        except ValueError as e:
            return f"cannot connect {s}->{t}: {e}", False
        edges.append({"source": s, "target": t})
        return f"connected {s}->{t}", True

    if tool == "delete_node":
        nid = args.get("id")
        if nid not in ids:
            return f"no node '{nid}'", False
        spec["nodes"] = [n for n in nodes if n["id"] != nid]
        spec["edges"] = [e for e in edges if e["source"] != nid and e["target"] != nid]
        return f"deleted {nid}", True

    return f"unknown tool '{tool}'", False


async def arun_coder(spec_in: dict, goal: str, deps: AgentDeps) -> AsyncIterator[dict]:
    spec = {
        "nodes": [dict(n) for n in (spec_in.get("nodes") or [])],
        "edges": [dict(e) for e in (spec_in.get("edges") or [])],
    }
    system = _system()
    history: list[tuple[str, str]] = []
    runs = 0

    for _ in range(_MAX_STEPS):
        user = _build_user(goal, spec, history)
        raw = await asyncio.to_thread(
            functools.partial(deps.llm.complete, system, user, model=deps.model, temperature=0.2)
        )
        act = _strip_to_json(raw)
        if not isinstance(act, dict):
            history.append(("(unparseable)", "Reply was not a JSON object. Send one JSON action."))
            continue

        thought = str(act.get("thought", ""))[:300]
        if act.get("done"):
            if spec["nodes"]:
                _layout(spec["nodes"], spec["edges"])
            yield {"type": "done", "message": str(act.get("message", "Done."))[:1000], "spec": spec}
            return

        tool = act.get("tool")
        args = act.get("args") or {}
        yield {"type": "step", "tool": tool, "args": args, "thought": thought}

        if tool == "run":
            runs += 1
            if runs > _MAX_RUNS:
                obs = "run limit reached; finish with done or make fewer runs"
                yield {"type": "obs", "text": obs, "ok": False}
                history.append((json.dumps(act)[:300], obs))
                continue
            try:
                ag._validate(spec["nodes"], spec["edges"])
            except ValueError as e:
                obs = f"cannot run: {e}"
                yield {"type": "obs", "text": obs, "ok": False}
                history.append((json.dumps(act)[:300], obs))
                continue
            results: dict[str, tuple[str, str]] = {}
            fatal = None
            async for fr in ag.arun_stream(spec, {}, deps):
                if fr.get("type") == "node":
                    yield {"type": "node", "id": fr["id"], "status": fr["status"]}
                    if fr["status"] in ("done", "error"):
                        results[fr["id"]] = (fr["status"], (fr.get("summary") or "").replace("\n", " ")[:160])
                elif fr.get("type") == "error":
                    fatal = fr.get("detail")
            errs = [f"{k}: {v[1]}" for k, v in results.items() if v[0] == "error"]
            if fatal:
                obs = f"run failed: {fatal}"
                ok = False
            elif errs:
                obs = "ran with errors -> " + "; ".join(errs)
                ok = False
            else:
                outs = "; ".join(f"{k}={v[1]}" for k, v in results.items())
                obs = f"ran clean ({len(results)} nodes). outputs: {outs}"[:600]
                ok = True
            yield {"type": "obs", "text": obs, "ok": ok}
            history.append((json.dumps(act)[:300], obs))
            continue

        # mutating tool
        obs, mutated = _apply(tool, args, spec)
        if mutated:
            _layout(spec["nodes"], spec["edges"])
            yield {"type": "spec", "spec": spec}
        yield {"type": "obs", "text": obs, "ok": mutated}
        history.append((json.dumps(act)[:300], obs))

    if spec["nodes"]:
        _layout(spec["nodes"], spec["edges"])
    yield {"type": "done", "message": f"Stopped after {_MAX_STEPS} steps. Kept the work so far.", "spec": spec}
