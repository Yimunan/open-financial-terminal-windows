"""Tests for the autonomous research loop's deterministic pure-logic helpers
(services.research_loop).

Hermetic: only the *pure* design/reflect plumbing is exercised — the objective ranking, the
clamp helper, the Experiment signature, the LLM-design coercion/clamp, the deterministic nudge +
ladder fallback, the `_design` de-dup contract, and the `_reflect` failed-check→next_change
fallback. A tiny `_FakeLLM` stands in for the model (`.structured` / `.complete`, or raising
variants to force the deterministic paths); no real LLM, network, or qhfi data lake is touched.
The data-bound phases (`_generate` / `_evaluate` / `_analyze` / `arun_research_loop`) need the
engine + lake and are deliberately NOT covered here.

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_research_loop.py -q`
"""

from __future__ import annotations

from dataclasses import asdict

from app.services import factors as fac
from app.services import research_loop as rl


# ── fakes / fixtures ─────────────────────────────────────────────────────────────────
class _FakeLLM:
    """Minimal LLM stand-in. ``structured``/``complete`` each return a canned value or raise.

    Pass an Exception instance (or a callable returning a value / raising) for either method to
    drive the deterministic fallback branches in `_design` / `_reflect`.
    """

    def __init__(self, structured=None, complete=None):
        self._structured = structured
        self._complete = complete
        self.structured_calls = 0
        self.complete_calls = 0

    def structured(self, system, user, schema, *, model=None):
        self.structured_calls += 1
        return self._resolve(self._structured)

    def complete(self, system, user, *, model=None, temperature=None):
        self.complete_calls += 1
        return self._resolve(self._complete)

    @staticmethod
    def _resolve(spec):
        if isinstance(spec, BaseException):
            raise spec
        if callable(spec):
            return spec()
        return spec


def _inventory():
    """Hand-built inventory (never call `_analyze`, which needs the registry + universe table)."""
    return {
        "factors": [
            {"key": "momentum", "label": "Momentum (12-1)", "kind": "price"},
            {"key": "value", "label": "Value (E/P)", "kind": "value"},
            {"key": "quality", "label": "Quality (ROE)", "kind": "quality"},
        ],
        "factor_keys": ["momentum", "value", "quality"],
        "universes": ["dow30"],
        "models": [],
    }


def _deps(llm):
    return rl.RLDeps(dm=None, fstore=None, fprov=None, llm=llm, model="m", store=None)


def _scored(n_checks_passed: int, sharpe: float) -> rl.Scored:
    return rl.Scored(
        metrics={"sharpe": sharpe},
        oos_sharpe=None,
        oos_sharpe_ratio=None,
        passed=False,
        checks={},
        n_checks_passed=n_checks_passed,
        notes=[],
    )


def _exp(**over) -> rl.Experiment:
    base = dict(factors=["momentum"], universe="dow30", mode="long_short",
                top_pct=0.2, gross=1.0, years=3, rebalance="monthly", timing="none")
    base.update(over)
    return rl.Experiment(**base)


# ── _objective ───────────────────────────────────────────────────────────────────────
def test_objective_orders_by_checks_then_sharpe():
    more_checks_low_sharpe = _scored(n_checks_passed=4, sharpe=0.1)
    fewer_checks_high_sharpe = _scored(n_checks_passed=3, sharpe=2.5)
    # "more checks always beats higher Sharpe": 4*100+0.1 > 3*100+2.5
    assert rl._objective(more_checks_low_sharpe) > rl._objective(fewer_checks_high_sharpe)
    assert rl._objective(_scored(2, 0.0)) == 200.0
    # within a tier, Sharpe is the tiebreaker
    assert rl._objective(_scored(2, 1.5)) > rl._objective(_scored(2, 0.5))


def test_objective_none_is_negative_infinity():
    assert rl._objective(None) == float("-inf")
    # a missing/None sharpe coerces to 0.0, never raises
    assert rl._objective(_scored(1, None)) == 100.0  # type: ignore[arg-type]


# ── _clampf ──────────────────────────────────────────────────────────────────────────
def test_clampf_clamps_and_defaults_on_garbage():
    assert rl._clampf(0.3, 0.05, 0.5, 0.2) == 0.3      # in range, untouched
    assert rl._clampf(99, 0.05, 0.5, 0.2) == 0.5       # clamped to hi
    assert rl._clampf(-1, 0.05, 0.5, 0.2) == 0.05      # clamped to lo
    assert rl._clampf("0.4", 0.05, 0.5, 0.2) == 0.4    # numeric string coerces
    assert rl._clampf(None, 0.05, 0.5, 0.2) == 0.2     # None → default
    assert rl._clampf("abc", 0.05, 0.5, 0.2) == 0.2    # garbage → default
    assert rl._clampf([1], 0.05, 0.5, 0.2) == 0.2      # non-coercible → default


# ── Experiment.signature ─────────────────────────────────────────────────────────────
def test_experiment_signature_is_factor_order_insensitive():
    a = _exp(factors=["value", "momentum"])
    b = _exp(factors=["momentum", "value"])
    assert a.signature() == b.signature()            # frozenset over factors

    # float fields are rounded to 2dp, so sub-1% differences collide
    near = _exp(top_pct=0.201, gross=1.004)
    assert near.signature() == _exp(top_pct=0.2, gross=1.0).signature()

    # but a real change to a signature field separates them
    assert _exp(mode="long_only").signature() != _exp(mode="long_short").signature()
    assert _exp(timing="trend").signature() != _exp(timing="none").signature()
    # rationale is NOT part of the signature
    assert _exp(rationale="x").signature() == _exp(rationale="y").signature()


# ── _coerce_experiment ───────────────────────────────────────────────────────────────
def test_coerce_experiment_filters_clamps_and_falls_back():
    inv = _inventory()
    raw = {
        "factors": ["value", "bogus", "momentum", "quality"],  # drop unknown, cap to 2
        "universe": "nope",                                    # unknown → dow30 fallback
        "mode": "sideways",                                    # bad → long_short
        "top_pct": 9.0,                                        # clamp → 0.5
        "gross": 0.0,                                          # clamp → 0.5
        "years": 99,                                           # clamp → 10
        "rebalance": "WEEKLY",                                 # bad → monthly
        "timing": "Magic",                                     # bad → none
        "rationale": "x" * 300,                                # truncate → 160
    }
    exp = rl._coerce_experiment(raw, inv)
    assert exp is not None
    assert exp.factors == ["value", "momentum"]   # unknown dropped, capped to 2, order preserved
    assert exp.universe == "dow30"
    assert exp.mode == "long_short"
    assert exp.top_pct == 0.5
    assert exp.gross == 0.5
    assert exp.years == 10
    assert exp.rebalance == "monthly"
    assert exp.timing == "none"
    assert len(exp.rationale) == 160

    # valid values are passed through (rebalance/timing case-normalized to lower)
    ok = rl._coerce_experiment(
        {"factors": ["momentum"], "universe": "dow30", "mode": "long_only",
         "top_pct": 0.3, "gross": 1.5, "years": 5, "rebalance": "Quarterly",
         "timing": "Trend", "rationale": "ok"},
        inv,
    )
    assert ok is not None
    assert (ok.mode, ok.rebalance, ok.timing, ok.years) == ("long_only", "quarterly", "trend", 5)


def test_coerce_experiment_unsalvageable_returns_none():
    inv = _inventory()
    assert rl._coerce_experiment(None, inv) is None              # not a dict
    assert rl._coerce_experiment({}, inv) is None                # no factors
    assert rl._coerce_experiment({"factors": []}, inv) is None   # empty factors
    assert rl._coerce_experiment({"factors": ["bogus"]}, inv) is None  # no valid factor keys


# ── _apply_nudge ─────────────────────────────────────────────────────────────────────
def test_apply_nudge_each_change_branch():
    src = _exp(factors=["momentum"], mode="long_short", gross=1.0, top_pct=0.2)

    # returns a NEW experiment, leaving the source untouched
    flipped = rl._apply_nudge(src, "flip_mode")
    assert flipped is not src
    assert flipped.mode == "long_only" and src.mode == "long_short"
    assert rl._apply_nudge(_exp(mode="long_only"), "flip_mode").mode == "long_short"

    assert rl._apply_nudge(src, "raise_gross").gross == 1.5
    assert rl._apply_nudge(_exp(gross=2.0), "raise_gross").gross == 2.0   # clamped at 2.0
    assert rl._apply_nudge(src, "lower_gross").gross == 0.5
    assert rl._apply_nudge(_exp(gross=0.5), "lower_gross").gross == 0.5   # clamped at 0.5

    assert rl._apply_nudge(src, "widen_quantile").top_pct == 0.3
    assert rl._apply_nudge(_exp(top_pct=0.5), "widen_quantile").top_pct == 0.5   # clamped
    assert rl._apply_nudge(src, "tighten_quantile").top_pct == 0.15
    assert rl._apply_nudge(_exp(top_pct=0.05), "tighten_quantile").top_pct == 0.05  # clamped

    # switch_factor rotates to the next CATALOG key (momentum → next key)
    order = list(fac.CATALOG.keys())
    expected_next = order[(order.index("momentum") + 1) % len(order)]
    switched = rl._apply_nudge(src, "switch_factor")
    assert switched.factors[0] == expected_next

    # unknown change is a no-op (signature preserved, but a fresh object)
    keep = rl._apply_nudge(src, "keep")
    assert keep is not src and keep.signature() == src.signature()


# ── _default_experiment ──────────────────────────────────────────────────────────────
def test_default_experiment_skips_tried_signatures():
    inv = _inventory()
    mem = rl.LoopMemory(goal="g")

    # first call → first ladder entry that has available factors: ["momentum"], long_short
    first = rl._default_experiment(mem, inv)
    assert first.factors == ["momentum"] and first.mode == "long_short"
    assert first.timing == "none"
    assert first.universe == "dow30"

    # record it as tried; the next default must differ (a not-yet-tried signature)
    mem.history.append(rl.IterationRecord(0, first, None, 0.0, None))
    second = rl._default_experiment(mem, inv)
    assert second.signature() != first.signature()
    # ladder's 2nd entry (value+momentum) — value is available in this inventory
    assert set(second.factors) == {"value", "momentum"}


def test_default_experiment_adds_trend_timing_on_second_lap():
    inv = _inventory()
    mem = rl.LoopMemory(goal="g")
    # fill history with >= len(_LADDER) records so the "second lap" trend overlay kicks in
    for i in range(len(rl._LADDER)):
        mem.history.append(rl.IterationRecord(i, _exp(rationale=f"r{i}"), None, 0.0, None))
    nxt = rl._default_experiment(mem, inv)
    assert nxt.timing == "trend"   # 2nd lap deterministically adds a trend-timing overlay


# ── _design de-dup contract ──────────────────────────────────────────────────────────
def test_design_dedups_repeated_llm_design():
    inv = _inventory()
    # The LLM keeps proposing the same already-tried config.
    repeat = {"factors": ["momentum"], "universe": "dow30", "mode": "long_short",
              "top_pct": 0.2, "gross": 1.0, "years": 3, "rebalance": "monthly",
              "timing": "none", "rationale": "repeat"}
    deps = _deps(_FakeLLM(structured=dict(repeat)))

    mem = rl.LoopMemory(goal="g")
    tried_exp = rl._coerce_experiment(dict(repeat), inv)
    assert tried_exp is not None
    mem.history.append(rl.IterationRecord(0, tried_exp, None, 0.0, None))
    mem.last_reflection = {"next_change": "flip_mode"}  # steer the nudge deterministically

    out = rl._design(deps, mem, inv)
    # the repeated signature must NOT be returned as-is
    assert out.signature() not in mem.tried_signatures()
    # with a flip_mode hint, the dedup nudge flips the mode to long_only
    assert out.mode == "long_only"


def test_design_uses_complete_fallback_when_structured_raises():
    inv = _inventory()
    good = '```json\n{"factors":["value"],"universe":"dow30","mode":"long_only",' \
           '"top_pct":0.25,"gross":1.0,"years":4,"rebalance":"quarterly","timing":"none",' \
           '"rationale":"from complete"}\n```'
    llm = _FakeLLM(structured=RuntimeError("no structured output"), complete=good)
    out = rl._design(_deps(llm), rl.LoopMemory(goal="g"), inv)
    assert llm.structured_calls == 1 and llm.complete_calls == 1  # fell through to complete()
    assert out.factors == ["value"] and out.mode == "long_only"
    assert out.rebalance == "quarterly"


def test_design_falls_back_to_ladder_when_llm_unusable():
    inv = _inventory()
    # both paths raise → deterministic ladder fallback (first ladder entry)
    llm = _FakeLLM(structured=RuntimeError("down"), complete=RuntimeError("down"))
    out = rl._design(_deps(llm), rl.LoopMemory(goal="g"), inv)
    assert out.factors == ["momentum"] and out.mode == "long_short"
    assert out.rationale == "deterministic fallback"


# ── _reflect deterministic fallback ──────────────────────────────────────────────────
def _reflect_with_failed_checks(failed: dict) -> dict:
    """Drive `_reflect` with an LLM that raises so the deterministic failed-check map is used."""
    scored = rl.Scored(
        metrics={"sharpe": 0.5, "calmar": 0.2, "max_drawdown": -0.4, "ann_turnover": 60.0},
        oos_sharpe=None, oos_sharpe_ratio=0.3, passed=False,
        checks=failed, n_checks_passed=sum(1 for v in failed.values() if v), notes=[],
    )
    mem = rl.LoopMemory(goal="g")
    mem.history.append(rl.IterationRecord(0, _exp(), scored, 0.0, None))
    llm = _FakeLLM(structured=RuntimeError("x"), complete=RuntimeError("x"))
    return rl._reflect(_deps(llm), mem)


def test_reflect_fallback_maps_failed_checks_to_changes():
    # turnover failing → tighten_quantile (highest priority)
    assert _reflect_with_failed_checks(
        {"turnover": False, "oos_robustness": False, "drawdown": False, "sharpe": False}
    )["next_change"] == "tighten_quantile"
    # oos_robustness failing (turnover ok) → switch_factor
    assert _reflect_with_failed_checks(
        {"turnover": True, "oos_robustness": False, "drawdown": False}
    )["next_change"] == "switch_factor"
    # drawdown failing → lower_gross
    assert _reflect_with_failed_checks(
        {"turnover": True, "oos_robustness": True, "drawdown": False}
    )["next_change"] == "lower_gross"
    # sharpe/calmar failing → flip_mode
    assert _reflect_with_failed_checks(
        {"turnover": True, "oos_robustness": True, "drawdown": True, "sharpe": False}
    )["next_change"] == "flip_mode"
    assert _reflect_with_failed_checks(
        {"turnover": True, "oos_robustness": True, "drawdown": True, "calmar": False}
    )["next_change"] == "flip_mode"
    # nothing failing → keep, and the result is always a valid _NEXT_CHANGES member
    out = _reflect_with_failed_checks({"turnover": True, "sharpe": True})
    assert out["next_change"] == "keep"
    assert out["next_change"] in rl._NEXT_CHANGES
    assert out["stop"] is False


def test_reflect_uses_structured_llm_when_available():
    mem = rl.LoopMemory(goal="g")
    mem.history.append(rl.IterationRecord(0, _exp(), _scored(2, 1.0), 0.0, None))
    canned = {"assessment": "blend value", "next_change": "switch_factor", "stop": False}
    out = rl._reflect(_deps(_FakeLLM(structured=dict(canned))), mem)
    assert out == canned


def test_reflect_no_history_suggests_switch_factor():
    out = rl._reflect(_deps(_FakeLLM(structured=RuntimeError("x"))), rl.LoopMemory(goal="g"))
    assert out["next_change"] == "switch_factor" and out["stop"] is False
