"""Sandboxed execution for user-authored `code` workflow steps.

A `code` node lets the user define a step's logic in Python. The terminal is local-first
(single user), but the backend can be reached over Tailscale, so we DO NOT exec arbitrary
Python: the source is parsed and validated against an AST allowlist (no imports, no dunder
access, no eval/exec/open/file/network builtins) before running in a curated namespace.

The user code receives upstream node outputs (`inputs` / `summaries`) plus a few read-only
helpers, and assigns `result` (any JSON-able value) and optional `summary` (text).
"""

from __future__ import annotations

import ast
import math
from typing import Any

import numpy as np
import pandas as pd

from app.services import market as mkt

# Bare names the user code may never reference (escape hatches / I/O / introspection).
_DENY_NAMES = {
    "eval", "exec", "compile", "open", "input", "__import__", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "exit", "quit", "breakpoint", "memoryview",
    "help", "copyright", "credits", "license", "object", "super", "classmethod", "staticmethod",
}

# Builtins exposed inside the sandbox (everything else is hidden).
_SAFE_BUILTINS = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float",
        "int", "len", "list", "map", "max", "min", "pow", "range", "round", "set",
        "sorted", "str", "sum", "tuple", "zip", "reversed", "isinstance", "print", "repr",
    )
}

STARTER_CODE = """\
# Define this step in Python. Available:
#   inputs[id]    -> upstream node's value  (keyed by source node id, e.g. inputs["n1"])
#   summaries[id] -> upstream node's text summary
#   pd, np, math  -> pandas / numpy / math
#   bars(sym)     -> OHLCV DataFrame   quote(sym) -> latest quote dict
# Assign `result` (any JSON-able value) and optionally `summary` (text).

vals = list(inputs.values())
result = vals[0] if vals else None
summary = f"passthrough: {result}"
"""


def _validate(src: str) -> ast.Module:
    """Parse and reject disallowed constructs. Raises ValueError with a clear message."""
    try:
        tree = ast.parse(src, mode="exec")
    except SyntaxError as e:
        raise ValueError(f"SyntaxError: {e.msg} (line {e.lineno})") from e

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("imports are not allowed in a code step")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr.endswith("__")):
            raise ValueError(f"dunder attribute access '{node.attr}' is not allowed")
        if isinstance(node, ast.Name) and node.id in _DENY_NAMES:
            raise ValueError(f"name '{node.id}' is not allowed in a code step")
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id.endswith("__")):
            raise ValueError(f"dunder name '{node.id}' is not allowed in a code step")
    return tree


def _jsonable(v: Any, _depth: int = 0) -> Any:
    """Best-effort conversion of a step result to JSON-serializable data."""
    if _depth > 6:
        return str(v)
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return [_jsonable(x, _depth + 1) for x in v.tolist()]
    if isinstance(v, pd.Series):
        return {str(k): _jsonable(x, _depth + 1) for k, x in v.head(500).items()}
    if isinstance(v, pd.DataFrame):
        return v.tail(500).reset_index().to_dict(orient="records")
    if isinstance(v, dict):
        return {str(k): _jsonable(x, _depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x, _depth + 1) for x in v]
    return str(v)


def run_user_code(src: str, inputs: dict[str, dict], deps: Any) -> dict:
    """Validate then run a user code step. Returns {value, summary}."""
    tree = _validate(src or "")

    def _bars(symbol: str, asset: str = "equity") -> pd.DataFrame:
        _, bars = mkt.fetch_bars(deps.dm, str(symbol).upper(), asset)
        return bars

    def _quote(symbol: str, asset: str = "equity") -> dict:
        _, bars = mkt.fetch_bars(deps.dm, str(symbol).upper(), asset)
        return mkt.quote_from_bars(bars)

    ns: dict[str, Any] = {
        "__builtins__": _SAFE_BUILTINS,
        "pd": pd,
        "np": np,
        "math": math,
        "bars": _bars,
        "quote": _quote,
        "inputs": {sid: res.get("value") for sid, res in inputs.items()},
        "summaries": {sid: res.get("summary", "") for sid, res in inputs.items()},
        "result": None,
        "summary": None,
    }
    exec(compile(tree, "<code-step>", "exec"), ns)  # noqa: S102 - sandboxed namespace, AST-allowlisted

    result = ns.get("result")
    summary = ns.get("summary")
    if summary is None:
        summary = str(result)
    return {"value": _jsonable(result), "summary": str(summary)[:8000]}
