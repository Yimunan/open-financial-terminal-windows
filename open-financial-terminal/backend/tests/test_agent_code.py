"""Tests for the `code` workflow-step sandbox (services.agent_code).

Security-critical: a `code` node lets the user author Python that the backend executes.
Because the terminal is reachable over Tailscale, the source is NOT run blindly — it is
parsed and screened against an AST allowlist (``_validate``) before exec in a curated
namespace. The bulk of this file pins that allowlist's *rejection* contract: imports,
dunder attribute/name access, and every member of ``_DENY_NAMES`` (eval/exec/open/getattr/
__import__/object/super/...) must raise ``ValueError``. The remainder pins the pure
result serializer (``_jsonable``) and an end-to-end ``run_user_code`` round-trip on a SAFE
snippet that never touches bars()/quote() — so the whole file is 100% hermetic (no
network, no LLM, no lake, no GPU).

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_agent_code.py -q`
"""

from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

from app.services import agent_code as ac


# --------------------------------------------------------------------------------------
# _validate — the AST allowlist (security-critical rejection contract)
# --------------------------------------------------------------------------------------


def test_validate_accepts_plain_python():
    """Plain expressions, comprehensions, and lambdas using only safe names parse clean."""
    for src in (
        "result = 1 + 2\nsummary = 'ok'",
        "result = [i * 2 for i in range(3)]",
        "f = lambda x: x + 1\nresult = f(41)",
        "result = sum(inputs.values()) if inputs else 0",
        "result = _private_local = 5",  # single leading underscore is NOT a dunder
    ):
        tree = ac._validate(src)
        # Returns the parsed module so run_user_code can compile it.
        assert tree is not None
        assert type(tree).__name__ == "Module"


def test_validate_rejects_imports():
    """Neither `import x` nor `from x import y` may appear in a code step."""
    with pytest.raises(ValueError, match="imports are not allowed"):
        ac._validate("import os")
    with pytest.raises(ValueError, match="imports are not allowed"):
        ac._validate("from os import getcwd")
    # An import buried below otherwise-fine code is still caught (ast.walk is whole-tree).
    with pytest.raises(ValueError, match="imports are not allowed"):
        ac._validate("result = 1\nimport sys")


def test_validate_rejects_dunder_attribute_and_name():
    """The classic sandbox-escape vectors: dunder attribute traversal and dunder names."""
    # x.__class__ / .__globals__ / .__subclasses__ chains start with a dunder *attribute*.
    with pytest.raises(ValueError, match="dunder attribute access '__class__'"):
        ac._validate("result = (1).__class__")
    with pytest.raises(ValueError, match="dunder attribute access"):
        ac._validate("result = obj.__dict__")
    # A bare dunder *name* (e.g. __builtins__) is rejected by the dunder-name rule,
    # independently of _DENY_NAMES (which does not list __builtins__).
    with pytest.raises(ValueError, match="dunder name '__builtins__'"):
        ac._validate("result = __builtins__")
    # The rule keys on a leading OR trailing double underscore, not just leading.
    with pytest.raises(ValueError, match="dunder attribute access 'foo__'"):
        ac._validate("result = x.foo__")


# A representative slice of _DENY_NAMES — the escape hatches / I/O / introspection builtins.
@pytest.mark.parametrize(
    "name",
    ["eval", "exec", "compile", "open", "__import__", "getattr", "setattr",
     "globals", "locals", "vars", "object", "super", "breakpoint", "input"],
)
def test_validate_rejects_denied_builtins(name):
    """Every sampled _DENY_NAMES member is rejected when referenced as a bare name."""
    assert name in ac._DENY_NAMES  # guard: the sample stays in sync with the real set
    with pytest.raises(ValueError, match=f"name '{name}' is not allowed"):
        ac._validate(f"result = {name}")


def test_validate_rejects_every_deny_name():
    """Exhaustive: NO member of _DENY_NAMES may slip through as a referenced name.

    Pins the full security set so adding a name to _DENY_NAMES without it actually
    being rejected (e.g. via a future regex/walk regression) fails loudly here.
    """
    for name in sorted(ac._DENY_NAMES):
        with pytest.raises(ValueError):
            ac._validate(f"x = {name}")


def test_validate_syntaxerror_becomes_valueerror():
    """A SyntaxError is repackaged as a ValueError carrying the offending line number."""
    with pytest.raises(ValueError) as ei:
        ac._validate("result = (")  # unclosed paren
    msg = str(ei.value)
    assert "SyntaxError" in msg and "line 1" in msg


# --------------------------------------------------------------------------------------
# _jsonable — best-effort JSON coercion of a step result
# --------------------------------------------------------------------------------------


def test_jsonable_converts_numpy_and_pandas():
    """numpy scalars/arrays and pandas Series/DataFrame degrade to JSON-able structures."""
    # np.int64 does NOT subclass int → routed through the np.generic .item() branch.
    out_i = ac._jsonable(np.int64(7))
    assert out_i == 7 and type(out_i) is int

    # ndarray → plain list.
    assert ac._jsonable(np.array([1, 2, 3])) == [1, 2, 3]

    # Series → {str(key): value}.
    assert ac._jsonable(pd.Series([10, 20], index=["a", "b"])) == {"a": 10, "b": 20}

    # DataFrame → records with the index reset into a column.
    recs = ac._jsonable(pd.DataFrame({"x": [1, 2]}))
    assert recs == [{"index": 0, "x": 1}, {"index": 1, "x": 2}]

    # Nested containers recurse; set is emitted as a list.
    nested = ac._jsonable({"k": [np.int64(1), {"y": 2}]})
    assert nested == {"k": [1, {"y": 2}]}
    assert sorted(ac._jsonable({3, 1, 2})) == [1, 2, 3]

    # An unknown object falls back to str().
    class Weird:
        def __repr__(self):
            return "WEIRD"

    assert ac._jsonable(Weird()) == "WEIRD"


def test_jsonable_numpy_float_passthrough_is_documented_gap():
    """KNOWN GAP (non-security): np.float64 is NOT normalized to a builtin float.

    The first _jsonable branch returns any `isinstance(v, (bool, int, float, str))`
    value unchanged. Because numpy's float64 *subclasses* Python ``float`` (whereas
    int64 does NOT subclass ``int``), an np.float64 short-circuits on that branch and
    never reaches the ``np.generic -> .item()`` branch — so it is returned still typed
    as np.float64. This contradicts the module's stated "np.generic -> python scalar"
    intent for floats specifically.

    It is benign today only because numpy 2.x's float64 remains json.dumps-serializable
    via its float ancestry; if that ever changes, run_user_code's `value` could carry a
    non-serializable scalar. Pinned to ACTUAL behavior (not greenwashed) so the gap is
    visible; tighten _jsonable's branch order to fix.
    """
    out = ac._jsonable(np.float64(1.5))
    assert out == 1.5
    assert isinstance(out, np.floating)  # <-- NOT a plain builtin float (the gap)
    # And the same leak happens nested inside a dict.
    nested = ac._jsonable({"y": np.float64(2.0)})
    assert isinstance(nested["y"], np.floating)


def test_jsonable_caps_recursion_depth():
    """Beyond depth 6 the recursion stops and stringifies, so a pathological nest is bounded."""
    # Nest a numpy scalar far deeper than the cap; the leaf must come back as a string,
    # never as a live numpy object.
    leaf = np.int64(99)
    nest: object = leaf
    for _ in range(12):
        nest = [nest]
    out = ac._jsonable(nest)
    cur = out
    while isinstance(cur, list) and cur:
        cur = cur[0]
    assert isinstance(cur, str)  # depth cap kicked in → str(), not a numpy scalar


# --------------------------------------------------------------------------------------
# run_user_code — end-to-end on a SAFE snippet (no bars()/quote(), no deps.dm use)
# --------------------------------------------------------------------------------------


@pytest.fixture()
def deps():
    """Dummy deps — dm is never touched because the snippets never call bars()/quote()."""
    return types.SimpleNamespace(dm=None)


def test_run_user_code_exposes_inputs_and_returns_jsonable(deps):
    """inputs/summaries are keyed by source-node id; result comes back JSON-able."""
    src = 'result = {"x": inputs["n1"], "s": summaries["n1"]}\nsummary = "ok"'
    inputs = {"n1": {"value": 5, "summary": "hello"}}
    out = ac.run_user_code(src, inputs, deps)
    assert out == {"value": {"x": 5, "s": "hello"}, "summary": "ok"}


def test_run_user_code_summary_defaults_and_truncates(deps):
    """summary defaults to str(result) when unset, and any summary is capped at 8000 chars."""
    # No `summary` assigned → defaults to str(result).
    out = ac.run_user_code('result = inputs["n1"] + 1', {"n1": {"value": 5}}, deps)
    assert out == {"value": 6, "summary": "6"}

    # Over-long summary is truncated to 8000.
    out2 = ac.run_user_code('result = 0\nsummary = "z" * 9000', {}, deps)
    assert out2["value"] == 0
    assert len(out2["summary"]) == 8000 and set(out2["summary"]) == {"z"}


def test_run_user_code_blocks_unsafe_source(deps):
    """run_user_code refuses to execute disallowed source — validation runs first."""
    with pytest.raises(ValueError, match="imports are not allowed"):
        ac.run_user_code("import os", {}, deps)
    with pytest.raises(ValueError, match="name 'eval' is not allowed"):
        ac.run_user_code("result = eval('1+1')", {}, deps)
    with pytest.raises(ValueError, match="dunder attribute access"):
        ac.run_user_code("result = (1).__class__", {}, deps)
