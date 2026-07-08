"""Autonomous research loop — design → generate → evaluate → reflect → redo (≤5 iterations).

This is the agent workflow that the visual graph builder can't express: a *self-directed*
research cycle that introspects the existing factors / strategies / models, designs a factor
experiment, runs it through the SAME qhfi engine path the Backtest widget uses, grades it
against the promotion Scorecard, reflects on the failing checks, and re-designs — carrying a
memory of everything tried plus the best-so-far.

The objective is **Scorecard satisfaction**: each iteration is graded by qhfi's `Scorecard`
gate (Sharpe≥1, Calmar≥0.5, max-dd≤25%, turnover≤50x, OOS robustness≥0.5). The loop searches
toward passing the most checks; a fully-passing config wins, Sharpe breaks ties.

Reuse, not reinvention:
  * In-sample weights + dashboard come from `backtest._weights_from_scores` + `backtest.shape_result`
    (identical to a single-factor `/api/backtest`), so blended runs render in the existing widget.
  * 2-factor blends z-score-combine `factors.build_signed` scores — the same recipe the built-in
    `value_momentum` composite uses.
  * OOS uses qhfi's `walk_forward` via a thin `_BlendStrategy` adapter whose `generate_weights`
    calls the *same* `build_signed` + `_weights_from_scores` on each causal price slice, so
    in-sample and out-of-sample scoring are guaranteed to agree.

Guards: the loop always produces results (LLM design failure → deterministic ladder), one bad
iteration never aborts the run, the iteration count is hard-capped at 5, and OOS gracefully
degrades to in-sample-only when history is too short for a single walk-forward fold.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, AsyncIterator

import pandas as pd
from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.validation import WalkForwardConfig, concat_oos, walk_forward
from qhfi.core.types import DateRange, Panel, TargetWeights, Universe
from qhfi.evaluation.scorecard import Scorecard
from qhfi.strategy.base import Strategy

from app.services import backtest as bt
from app.services import factors as fac
from app.services import registry as reg
from app.services.agent_assistant import _strip_to_json
from app.services.universe import get_universe, list_universes

_MAX_ITERS = 2        # default iteration count (kept low — each iteration is a full backtest)
_MAX_ITERS_CEIL = 15  # hard ceiling (the committee-portfolio workflow runs up to this)
_MODES = ("long_only", "long_short")
_REBALS = ("monthly", "quarterly", "annual")
_TIMINGS = ("none", "trend", "regime")  # market-timing overlay choices
#: deterministic exploration ladder when the LLM is unavailable / repeats itself.
_LADDER: list[tuple[list[str], str]] = [
    (["momentum"], "long_short"),
    (["value", "momentum"], "long_short"),
    (["quality", "momentum"], "long_short"),
    (["reversal"], "long_short"),
    (["volatility"], "long_only"),
]
_NEXT_CHANGES = (
    "switch_factor", "flip_mode", "raise_gross", "lower_gross",
    "widen_quantile", "tighten_quantile", "change_universe", "keep",
)


# ── carried state ──────────────────────────────────────────────────────────────────
@dataclass
class RLDeps:
    dm: Any
    fstore: Any
    fprov: Any
    llm: Any
    model: str
    store: Any


@dataclass
class Experiment:
    """One iteration's design. ``top_pct`` is the long/short tail fraction; ``gross`` scales the
    book's gross exposure (1.0 = the engine's default 100%)."""

    factors: list[str]
    universe: str
    mode: str
    top_pct: float
    gross: float
    years: int
    rebalance: str
    timing: str = "none"  # market-timing overlay: "none" | "trend" | "regime"
    rationale: str = ""

    def signature(self) -> tuple:
        return (
            frozenset(self.factors), self.universe, self.mode,
            round(self.top_pct, 2), round(self.gross, 2), self.years, self.rebalance, self.timing,
        )


@dataclass
class Scored:
    metrics: dict
    oos_sharpe: float | None
    oos_sharpe_ratio: float | None
    passed: bool
    checks: dict
    n_checks_passed: int
    notes: list[str]


@dataclass
class IterationRecord:
    i: int
    experiment: Experiment
    scored: Scored | None
    objective: float
    result_payload: dict | None
    error: str | None = None


@dataclass
class LoopMemory:
    goal: str
    history: list[IterationRecord] = field(default_factory=list)
    best: IterationRecord | None = None
    last_reflection: dict | None = None
    guidance: str | None = None  # external reviewer feedback (e.g. a committee critique) to address

    def tried_signatures(self) -> set[tuple]:
        return {r.experiment.signature() for r in self.history}


def _objective(scored: Scored | None) -> float:
    """Single source of truth for 'better': pass the most Scorecard checks (×100), then Sharpe.

    Encodes the user's pass/fail-Scorecard choice — a config that clears more gate checks always
    ranks above one that clears fewer; in-sample Sharpe is the within-tier tiebreaker.
    """
    if scored is None:
        return float("-inf")
    sharpe = float(scored.metrics.get("sharpe", 0.0) or 0.0)
    return scored.n_checks_passed * 100.0 + sharpe


# ── factor blending (mirrors factors.build_signed's composite recipe) ────────────────
def _blend_scores(deps: RLDeps, universe: Universe, prices: Panel, factor_keys: list[str]) -> Panel:
    """Signed composite score (higher = long) over 1–2 factors: z-score each component
    cross-sectionally and average per cell over the components that have data."""
    signed = [fac.build_signed(deps.dm, deps.fstore, deps.fprov, universe, prices, k) for k in factor_keys]
    if len(signed) == 1:
        return signed[0]
    acc = cnt = None
    for s in signed:
        z = fac._zscore_rows(s).reindex(index=prices.index, columns=prices.columns)
        mask = z.notna().astype(float)
        acc = z.fillna(0.0) if acc is None else acc + z.fillna(0.0)
        cnt = mask if cnt is None else cnt + mask
    return acc / cnt.where(cnt > 0)


class _BlendStrategy(Strategy):
    """qhfi `Strategy` adapter so `walk_forward` can drive the exact same scoring + weighting as
    the in-sample run. Stateless/causal: `generate_weights` derives weights from the passed price
    slice only (the engine's one-bar lag is the look-ahead guard)."""

    name = "research_blend"

    def __init__(self, deps: RLDeps, factor_keys: list[str], mode: str, top_pct: float, freq: str) -> None:
        super().__init__()
        self._deps = deps
        self._keys = factor_keys
        self._mode = mode
        self._top_pct = top_pct
        self._freq = freq

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        scores = _blend_scores(self._deps, universe, prices, self._keys)
        return bt._weights_from_scores(scores, self._mode, self._top_pct, self._freq)


# ── phase 1: analyze ─────────────────────────────────────────────────────────────────
def _analyze(deps: RLDeps) -> dict:
    """Introspect the existing factors / models / universes (metadata only — no data fetch)."""
    try:
        factors = [{"key": k, "label": v["label"], "kind": v["kind"]} for k, v in fac.CATALOG.items()]
    except Exception:  # noqa: BLE001
        factors = []
    try:
        models = [m.get("name") for m in reg.list_models(deps.store).get("models", [])]
    except Exception:  # noqa: BLE001
        models = []
    universes = list_universes() or ["dow30"]
    return {
        "factors": factors,
        "factor_keys": [f["key"] for f in factors],
        "universes": universes,
        "models": models,
    }


# ── phase 2: design (LLM, with deterministic fallback) ───────────────────────────────
def _design_schema(inventory: dict) -> dict:
    keys = inventory["factor_keys"] or list(fac.CATALOG.keys())
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["factors", "universe", "mode", "top_pct", "gross", "years", "rebalance", "timing", "rationale"],
        "properties": {
            "factors": {"type": "array", "minItems": 1, "maxItems": 2,
                        "items": {"type": "string", "enum": keys}},
            "universe": {"type": "string", "enum": inventory["universes"]},
            "mode": {"type": "string", "enum": list(_MODES)},
            "top_pct": {"type": "number", "minimum": 0.05, "maximum": 0.5},
            "gross": {"type": "number", "minimum": 0.5, "maximum": 2.0},
            "years": {"type": "integer", "minimum": 1, "maximum": 10},
            "rebalance": {"type": "string", "enum": list(_REBALS)},
            "timing": {"type": "string", "enum": list(_TIMINGS)},
            "rationale": {"type": "string", "maxLength": 160},
        },
    }


def _design_system(inventory: dict) -> str:
    factors = ", ".join(f"{f['key']} ({f['label']})" for f in inventory["factors"])
    universes = ", ".join(inventory["universes"])
    return (
        "You are a quantitative research agent designing a cross-sectional factor experiment. "
        "You blend 1-2 factors into a long/short or long-only book and your goal is to PASS the "
        "promotion scorecard: Sharpe>=1, Calmar>=0.5, max drawdown<=25%, annual turnover<=50x, "
        "and OOS/in-sample Sharpe ratio>=0.5. Emit ONE JSON object with this exact shape:\n"
        '{"factors":[<1-2 keys>],"universe":<name>,"mode":"long_only"|"long_short",'
        '"top_pct":0.05-0.5,"gross":0.5-2.0,"years":1-10,'
        '"rebalance":"monthly"|"quarterly"|"annual","timing":"none"|"trend"|"regime",'
        '"rationale":"<short why>"}\n\n'
        f"Factors: {factors}.\nUniverses: {universes}.\n\n"
        "Lower top_pct and longer rebalance reduce turnover; blending a value/quality factor with "
        "momentum tends to improve robustness. Value/quality factors need an equity universe. "
        "A market-timing overlay can lift risk-adjusted return: `trend` scales the book to cash when "
        "the benchmark is below its moving average; `regime` cuts exposure in turbulent volatility "
        "regimes; `none` stays fully invested. Use timing to raise Sharpe when drawdowns hurt it. "
        "Reply with ONLY the JSON object."
    )


def _design_user(memory: LoopMemory) -> str:
    lines = [f"Research goal: {memory.goal}"]
    if memory.history:
        lines.append("\nTried so far (factors | mode | universe | top_pct -> sharpe, oos_ratio, checks passed):")
        for r in memory.history[-6:]:
            s = r.scored
            tag = (f"sharpe {s.metrics.get('sharpe'):.2f}, oos {s.oos_sharpe_ratio if s.oos_sharpe_ratio is None else round(s.oos_sharpe_ratio, 2)}, "
                   f"{s.n_checks_passed} checks" if s else f"ERROR: {r.error}")
            e = r.experiment
            lines.append(f"  - {'+'.join(e.factors)} | {e.mode} | {e.universe} | {e.top_pct} -> {tag}")
    if memory.best and memory.best.scored:
        b = memory.best.experiment
        lines.append(f"\nBest so far: {'+'.join(b.factors)} | {b.mode} | {b.universe} "
                     f"({memory.best.scored.n_checks_passed} checks passed).")
    if memory.last_reflection:
        lines.append(f"\nSuggested next change: {memory.last_reflection.get('next_change', 'keep')} "
                     f"— {memory.last_reflection.get('assessment', '')}")
    if memory.guidance:
        lines.append("\nA strategy review board raised these points to address — incorporate them "
                     f"into the next experiment:\n{memory.guidance.strip()[:800]}")
    lines.append("\nDesign the next experiment. Reply with ONLY the JSON object.")
    return "\n".join(lines)


def _clampf(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def _coerce_experiment(raw: dict, inventory: dict) -> Experiment | None:
    """Validate + clamp an LLM design into a runnable Experiment, or None if unsalvageable."""
    if not isinstance(raw, dict):
        return None
    keys = inventory["factor_keys"]
    universes = inventory["universes"]
    factors = [k for k in (raw.get("factors") or []) if k in keys][:2]
    if not factors:
        return None
    universe = raw.get("universe")
    if universe not in universes:
        universe = "dow30" if "dow30" in universes else universes[0]
    mode = raw.get("mode") if raw.get("mode") in _MODES else "long_short"
    rebalance = str(raw.get("rebalance", "monthly")).lower()
    if rebalance not in _REBALS:
        rebalance = "monthly"
    timing = str(raw.get("timing", "none")).lower()
    if timing not in _TIMINGS:
        timing = "none"
    return Experiment(
        factors=factors,
        universe=universe,
        mode=mode,
        top_pct=_clampf(raw.get("top_pct", 0.2), 0.05, 0.5, 0.2),
        gross=_clampf(raw.get("gross", 1.0), 0.5, 2.0, 1.0),
        years=int(_clampf(raw.get("years", 3), 1, 10, 3)),
        rebalance=rebalance,
        timing=timing,
        rationale=str(raw.get("rationale", ""))[:160],
    )


def _default_experiment(memory: LoopMemory, inventory: dict) -> Experiment:
    """Deterministic fallback so the loop always produces results: walk the ladder, skipping any
    config already tried, and snap factors/universe to what's actually available."""
    universes = inventory["universes"]
    keys = set(inventory["factor_keys"])
    universe = "dow30" if "dow30" in universes else universes[0]
    tried = memory.tried_signatures()
    # On the second lap of the ladder, deterministically add a trend-timing overlay as a fresh lever.
    timing = "trend" if len(memory.history) >= len(_LADDER) else "none"
    candidates = _LADDER + [([k], "long_short") for k in inventory["factor_keys"]]
    for factors, mode in candidates:
        fs = [k for k in factors if k in keys]
        if not fs:
            continue
        exp = Experiment(fs, universe, mode, 0.2, 1.0, 3, "monthly", timing, "deterministic fallback")
        if exp.signature() not in tried:
            return exp
    # everything tried — nudge top_pct + flip timing so the signature differs
    base = _LADDER[len(memory.history) % len(_LADDER)]
    fs = [k for k in base[0] if k in keys] or [next(iter(keys), "momentum")]
    return Experiment(fs, universe, base[1], 0.15, 1.0, 3, "monthly",
                      "trend" if timing == "none" else "regime", "deterministic fallback (nudged)")


def _apply_nudge(exp: Experiment, change: str) -> Experiment:
    """Deterministically perturb an experiment per a reflection's `next_change` (used when the
    LLM repeats a tried config, so each of the 5 iterations explores something new)."""
    e = Experiment(**asdict(exp))
    if change == "flip_mode":
        e.mode = "long_only" if e.mode == "long_short" else "long_short"
    elif change == "raise_gross":
        e.gross = min(2.0, round(e.gross + 0.5, 2))
    elif change == "lower_gross":
        e.gross = max(0.5, round(e.gross - 0.5, 2))
    elif change == "widen_quantile":
        e.top_pct = min(0.5, round(e.top_pct + 0.1, 2))
    elif change == "tighten_quantile":
        e.top_pct = max(0.05, round(e.top_pct - 0.05, 2))
    elif change == "switch_factor" and e.factors:
        order = list(fac.CATALOG.keys())
        cur = e.factors[0]
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
        e.factors = [nxt] + e.factors[1:]
    return e


def _design(deps: RLDeps, memory: LoopMemory, inventory: dict) -> Experiment:
    """Pick the next experiment. Tries the LLM (structured → complete+strip), falls back to the
    deterministic ladder, and guarantees the result is a NOT-yet-tried signature."""
    exp: Experiment | None = None
    system = _design_system(inventory)
    user = _design_user(memory)
    schema = _design_schema(inventory)
    try:
        raw = deps.llm.structured(system, user, schema, model=deps.model)
        exp = _coerce_experiment(raw, inventory)
    except Exception:  # noqa: BLE001 - structured output unsupported / proxy down / garbled
        exp = None
    if exp is None:
        try:
            txt = deps.llm.complete(system, user, model=deps.model, temperature=0.3)
            exp = _coerce_experiment(_strip_to_json(txt) or {}, inventory)
        except Exception:  # noqa: BLE001
            exp = None
    if exp is None:
        return _default_experiment(memory, inventory)

    # De-dup: if the LLM repeated a config, nudge it (using the last reflection's hint) or fall
    # back to the deterministic ladder so the 5 iterations stay distinct.
    tried = memory.tried_signatures()
    if exp.signature() in tried:
        change = (memory.last_reflection or {}).get("next_change", "switch_factor")
        nudged = _apply_nudge(exp, change if change in _NEXT_CHANGES else "switch_factor")
        exp = nudged if nudged.signature() not in tried else _default_experiment(memory, inventory)
    return exp


# ── phase 3: generate (in-sample dashboard + raw result + OOS) ───────────────────────
def _fetch_prices(deps: RLDeps, universe: Universe, years: int) -> tuple[Panel, date, date]:
    """Warm + load the close panel exactly like backtest.run_backtest (200-day factor warm-up)."""
    today = date.today()
    win_end_d = today
    win_start_d = win_end_d - timedelta(days=int(years * 365.25))
    span = DateRange(start=win_start_d - timedelta(days=200), end=win_end_d)
    deps.dm.update(universe, span)
    prices = deps.dm.get_panel(universe, "close", span)
    return prices, win_start_d, win_end_d


def _generate(deps: RLDeps, exp: Experiment) -> tuple[dict, Any, pd.Series | None]:
    """Run the experiment: returns (dashboard payload, raw in-sample BacktestResult, oos returns)."""
    universe = get_universe(exp.universe)
    prices, win_start_d, win_end_d = _fetch_prices(deps, universe, exp.years)
    if prices.empty or prices.shape[1] < 3:
        raise ValueError(f"insufficient data for universe '{exp.universe}'")

    freq = bt._REBAL_FREQ.get(exp.rebalance, "M")
    scores = _blend_scores(deps, universe, prices, exp.factors)
    weights = bt._weights_from_scores(scores, exp.mode, exp.top_pct, freq)
    if exp.gross != 1.0:
        weights = weights * exp.gross

    result = BacktestEngine(initial_equity=100_000.0).run(weights, prices, universe)

    # Market-timing overlay (mirrors backtest.run_backtest): scale the book per-date by a benchmark
    # exposure signal, then re-run. `none` keeps the single run above. Falls back to fully-invested
    # if there's no investable benchmark (e.g. a crypto-only universe).
    timing_diag = None
    if exp.timing in ("trend", "regime"):
        pair = bt._timing_exposure(deps.dm, universe, prices, result.returns, {"kind": exp.timing})
        if pair is not None:
            exposure, diag = pair
            exposure = exposure.reindex(weights.index).ffill().fillna(1.0)
            base_result = result
            weights = weights.mul(exposure, axis=0).fillna(0.0)
            result = BacktestEngine(initial_equity=100_000.0).run(weights, prices, universe)
            timing_diag = bt._timing_block(
                diag, exposure, base_result, result, win_start_d, win_end_d,
                100_000.0, bt._periods_per_year(universe),
            )

    # OOS via walk-forward over the same factor scoring (None if history is too short for one fold).
    # The overlay is a portfolio-level risk control; OOS measures the *signal's* persistence, so the
    # walk-forward strategy stays untimed (a deliberate simplification).
    oos: pd.Series | None = None
    try:
        strat = _BlendStrategy(deps, exp.factors, exp.mode, exp.top_pct, freq)
        folds = walk_forward(strat, prices, universe, BacktestEngine(initial_equity=100_000.0), WalkForwardConfig())
        if folds:
            o = concat_oos(folds)
            oos = o if len(o) else None
    except Exception:  # noqa: BLE001 - OOS is best-effort; in-sample grade still proceeds
        oos = None

    try:
        bench, bench_label = bt.index_benchmark_returns(deps.dm, universe, prices.index)
    except Exception:  # noqa: BLE001
        bench, bench_label = None, "equal-weight"

    payload = bt.shape_result(
        result, prices, universe, exp.universe, win_start_d, win_end_d, 100_000.0,
        label_extra={"factor": " + ".join(exp.factors), "mode": exp.mode,
                     "rebalance": exp.rebalance, "timing": exp.timing},
        bench=bench, bench_label=bench_label, timing=timing_diag,
    )
    return payload, result, oos


# ── phase 4: evaluate (Scorecard gate) ───────────────────────────────────────────────
def _evaluate(result: Any, oos: pd.Series | None, universe: Universe) -> Scored:
    ppy = bt._periods_per_year(universe)
    card = Scorecard().grade(result, oos, ppy)
    is_sharpe = float(card.metrics.get("sharpe", 0.0) or 0.0)
    oos_sharpe = card.metrics.get("oos_sharpe")
    ratio = (float(oos_sharpe) / is_sharpe) if (oos_sharpe is not None and is_sharpe > 0) else None
    notes = list(card.notes)
    if oos is None:
        notes.append("OOS: insufficient history for a walk-forward fold (graded in-sample only)")
    return Scored(
        metrics={k: (round(float(v), 4) if isinstance(v, (int, float)) else v) for k, v in card.metrics.items()},
        oos_sharpe=None if oos_sharpe is None else round(float(oos_sharpe), 4),
        oos_sharpe_ratio=None if ratio is None else round(ratio, 4),
        passed=bool(card.passed),
        checks=dict(card.checks),
        n_checks_passed=sum(1 for v in card.checks.values() if v),
        notes=notes,
    )


# ── phase 5: reflect (LLM, advisory only) ────────────────────────────────────────────
_REFLECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["assessment", "next_change", "stop"],
    "properties": {
        "assessment": {"type": "string", "maxLength": 200},
        "next_change": {"type": "string", "enum": list(_NEXT_CHANGES)},
        "stop": {"type": "boolean"},
    },
}


def _reflect(deps: RLDeps, memory: LoopMemory) -> dict:
    """Short assessment + an enumerated next-step hint aimed at the failing checks. Advisory: the
    next `_design` re-validates independently, so a bad reflection can't break the loop."""
    last = memory.history[-1] if memory.history else None
    if last is None or last.scored is None:
        return {"assessment": "previous iteration errored; trying a different factor",
                "next_change": "switch_factor", "stop": False}
    s = last.scored
    failed = [k for k, v in s.checks.items() if not v]
    system = (
        "You are reviewing a factor backtest against the promotion scorecard (Sharpe>=1, "
        "Calmar>=0.5, drawdown<=25%, turnover<=50x, OOS/IS Sharpe>=0.5). Suggest ONE change to "
        "pass more checks next time. Emit ONE JSON object: "
        '{"assessment":"<short>","next_change":<one of: ' + ", ".join(_NEXT_CHANGES) + '>,"stop":false}. '
        "Map failures to changes: high turnover -> tighten_quantile or (handled elsewhere) longer "
        "rebalance; weak OOS robustness -> switch_factor or blend in value/quality; low Sharpe -> "
        "switch_factor or flip_mode; deep drawdown -> lower_gross or flip to long_only. Reply with ONLY JSON."
    )
    user = (
        f"Goal: {memory.goal}\n"
        f"Last experiment: {'+'.join(last.experiment.factors)} | {last.experiment.mode} | "
        f"{last.experiment.universe} | top_pct={last.experiment.top_pct} | gross={last.experiment.gross} | "
        f"rebalance={last.experiment.rebalance}\n"
        f"Metrics: sharpe={s.metrics.get('sharpe')}, calmar={s.metrics.get('calmar')}, "
        f"max_drawdown={s.metrics.get('max_drawdown')}, ann_turnover={s.metrics.get('ann_turnover')}, "
        f"oos_ratio={s.oos_sharpe_ratio}\n"
        f"Failed checks: {failed or 'none — passed!'}\n"
        + (f"A strategy review board advised: {memory.guidance.strip()[:400]}\n" if memory.guidance else "")
        + "Suggest the next change as a single JSON object."
    )
    try:
        out = deps.llm.structured(system, user, _REFLECT_SCHEMA, model=deps.model)
        if isinstance(out, dict) and out.get("next_change") in _NEXT_CHANGES:
            return out
    except Exception:  # noqa: BLE001
        pass
    try:
        out = _strip_to_json(deps.llm.complete(system, user, model=deps.model, temperature=0.3)) or {}
        if out.get("next_change") in _NEXT_CHANGES:
            return out
    except Exception:  # noqa: BLE001
        pass
    # deterministic fallback: target the worst failing check
    change = "keep"
    if "turnover" in failed:
        change = "tighten_quantile"
    elif "oos_robustness" in failed:
        change = "switch_factor"
    elif "drawdown" in failed:
        change = "lower_gross"
    elif "sharpe" in failed or "calmar" in failed:
        change = "flip_mode"
    return {"assessment": f"failed: {', '.join(failed) or 'none'}", "next_change": change, "stop": False}


# ── the streamed loop ────────────────────────────────────────────────────────────────
def _compact(record: IterationRecord) -> dict:
    """Small per-iteration summary for the `iteration`/`done` frames + persistence (no curves)."""
    e = record.experiment
    s = record.scored
    tlabel = "" if e.timing == "none" else f" · {e.timing} timing"
    return {
        "i": record.i,
        "label": f"{' + '.join(e.factors)} · {e.mode} · {e.universe} · {e.years}y{tlabel}",
        "experiment": asdict(e),
        "objective": round(record.objective, 4) if record.objective != float("-inf") else None,
        "passed": bool(s.passed) if s else False,
        "n_checks_passed": s.n_checks_passed if s else 0,
        "checks": s.checks if s else {},
        "metrics": {k: s.metrics.get(k) for k in ("sharpe", "calmar", "max_drawdown", "ann_turnover", "cagr")} if s else {},
        "oos_sharpe_ratio": s.oos_sharpe_ratio if s else None,
        "error": record.error,
    }


async def arun_research_loop(
    goal: str, deps: RLDeps, *, max_iters: int = _MAX_ITERS, run_id: str | None = None,
    guidance: str | None = None,
) -> AsyncIterator[dict]:
    """Drive the design→generate→evaluate→reflect cycle, streaming frames per phase.

    Frame types: started · analyze · phase · design · evaluate · result · iteration · reflect ·
    error · done. Every iteration is persisted as soon as it's scored (so a mid-run disconnect
    keeps partial results). Runs all `max_iters` (default 2, hard cap 15) iterations unless a reflection sets stop.
    ``guidance`` optionally biases design/reflect toward external reviewer feedback.
    """
    max_iters = max(1, min(int(max_iters or _MAX_ITERS), _MAX_ITERS_CEIL))
    memory = LoopMemory(goal=goal, guidance=(guidance or None))

    inventory = await asyncio.to_thread(_analyze, deps)
    yield {"type": "analyze", "iteration": 0, "inventory": inventory}

    for i in range(max_iters):
        # design
        yield {"type": "phase", "iteration": i, "phase": "design", "status": "running"}
        exp = await asyncio.to_thread(_design, deps, memory, inventory)
        yield {"type": "design", "iteration": i, "experiment": asdict(exp)}

        # generate
        yield {"type": "phase", "iteration": i, "phase": "generate", "status": "running"}
        try:
            payload, result, oos = await asyncio.to_thread(_generate, deps, exp)
        except Exception as e:  # noqa: BLE001 - one bad iteration never aborts the run
            rec = IterationRecord(i, exp, None, float("-inf"), None, error=f"{type(e).__name__}: {e}")
            memory.history.append(rec)
            if deps.store and run_id:
                try:
                    deps.store.add_research_iteration(run_id, i, _persist_blob(rec, None))
                except Exception:  # noqa: BLE001
                    pass
            yield {"type": "error", "iteration": i, "phase": "generate", "detail": rec.error}
            yield {"type": "iteration", "iteration": i, "record": _compact(rec),
                   "best": _compact(memory.best) if memory.best else None}
            continue

        # evaluate
        yield {"type": "phase", "iteration": i, "phase": "evaluate", "status": "running"}
        universe = get_universe(exp.universe)
        scored = await asyncio.to_thread(_evaluate, result, oos, universe)
        rec = IterationRecord(i, exp, scored, _objective(scored), payload)
        memory.history.append(rec)
        if memory.best is None or rec.objective > memory.best.objective:
            memory.best = rec

        if deps.store and run_id:
            try:
                deps.store.add_research_iteration(run_id, i, _persist_blob(rec, payload))
            except Exception:  # noqa: BLE001 - persistence failure shouldn't kill the stream
                pass

        yield {"type": "evaluate", "iteration": i, "scored": _scored_frame(scored)}
        yield {"type": "result", "iteration": i, "payload": payload}
        yield {"type": "iteration", "iteration": i, "record": _compact(rec),
               "best": _compact(memory.best) if memory.best else None}

        # reflect (skip after the final iteration — nothing consumes it)
        if i < max_iters - 1:
            yield {"type": "phase", "iteration": i, "phase": "reflect", "status": "running"}
            reflection = await asyncio.to_thread(_reflect, deps, memory)
            memory.last_reflection = reflection
            yield {"type": "reflect", "iteration": i, "text": reflection.get("assessment", ""),
                   "next_change": reflection.get("next_change", "keep")}
            if reflection.get("stop"):
                break

    if deps.store and run_id and memory.best:
        try:
            deps.store.finalize_research_run(run_id, _compact(memory.best))
        except Exception:  # noqa: BLE001
            pass

    yield {"type": "done", "run_id": run_id,
           "best_iteration": memory.best.i if memory.best else None,
           "best": _compact(memory.best) if memory.best else None,
           "n_iterations": len(memory.history)}


def run_loop_sync(
    goal: str, deps: RLDeps, *, max_iters: int = 3, guidance: str | None = None,
    target_sharpe: float | None = None, use_committee: bool = False, build_portfolio: bool = False,
    on_event: Callable[[dict], None] = lambda _f: None,
) -> dict:
    """Synchronous design→generate→evaluate→reflect loop for callers that just want the final
    result (e.g. the Agent-workflow ``research`` node) — no streaming, no persistence. Reuses the
    same phase functions as ``arun_research_loop`` and the same per-iteration error isolation.

    ``guidance``      — external reviewer feedback (committee critique) that biases design/reflect.
    ``target_sharpe`` — early-stop once the best in-sample Sharpe meets this (the "satisfied" gate).
    ``use_committee`` — after each iteration, run a local strategy-approval committee on the best and
                        fold its verdict into ``guidance`` so every redo is committee-informed.
    ``build_portfolio``— after the loop, persist the winner as a rebalancing portfolio.

    Returns ``{best, history, n_iterations, target_sharpe, target_met, best_sharpe, timing, portfolio}``."""
    max_iters = max(1, min(int(max_iters or 3), _MAX_ITERS_CEIL))
    memory = LoopMemory(goal=goal, guidance=(guidance or None))
    inventory = _analyze(deps)
    target_met = False
    for i in range(max_iters):
        on_event({"type": "phase", "iteration": i, "phase": "design", "status": "running"})
        exp = _design(deps, memory, inventory)
        on_event({"type": "design", "iteration": i, "experiment": asdict(exp)})
        on_event({"type": "phase", "iteration": i, "phase": "generate", "status": "running"})
        try:
            payload, result, oos = _generate(deps, exp)
        except Exception as e:  # noqa: BLE001 - one bad iteration never aborts the loop
            rec = IterationRecord(i, exp, None, float("-inf"), None, error=f"{type(e).__name__}: {e}")
            memory.history.append(rec)
            on_event({"type": "error", "iteration": i, "phase": "generate", "detail": rec.error})
            on_event({"type": "iteration", "iteration": i, "record": _compact(rec),
                      "best": _compact(memory.best) if memory.best else None})
            continue
        on_event({"type": "phase", "iteration": i, "phase": "evaluate", "status": "running"})
        scored = _evaluate(result, oos, get_universe(exp.universe))
        rec = IterationRecord(i, exp, scored, _objective(scored), payload)
        memory.history.append(rec)
        if memory.best is None or rec.objective > memory.best.objective:
            memory.best = rec
        on_event({"type": "iteration", "iteration": i, "record": _compact(rec),
                  "best": _compact(memory.best) if memory.best else None})
        # Satisfaction gate: stop as soon as the best meets the Sharpe target.
        if target_sharpe is not None and _best_sharpe(memory) >= target_sharpe:
            target_met = True
            break
        if i < max_iters - 1:
            on_event({"type": "phase", "iteration": i, "phase": "reflect", "status": "running"})
            # In-loop committee review steers the next design; else the standard reflection.
            if use_committee:
                memory.guidance = _committee_guidance(deps, memory) or memory.guidance
            memory.last_reflection = _reflect(deps, memory)
            on_event({"type": "reflect", "iteration": i,
                      "text": memory.last_reflection.get("assessment", ""),
                      "next_change": memory.last_reflection.get("next_change", "keep")})

    out: dict = {
        "best": _compact(memory.best) if memory.best else None,
        "history": [_compact(r) for r in memory.history],
        "n_iterations": len(memory.history),
        "target_sharpe": target_sharpe,
        "target_met": target_met,
        "best_sharpe": _best_sharpe(memory) if memory.best else None,
        "timing": memory.best.experiment.timing if memory.best else None,
    }
    if build_portfolio and memory.best and memory.best.result_payload:
        try:
            out["portfolio"] = _build_portfolio(deps, memory.best)
        except Exception as e:  # noqa: BLE001 - portfolio build is best-effort, never aborts the result
            out["portfolio"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def _best_sharpe(memory: LoopMemory) -> float:
    if memory.best and memory.best.scored:
        return float(memory.best.scored.metrics.get("sharpe", float("-inf")) or float("-inf"))
    return float("-inf")


def _committee_guidance(deps: RLDeps, memory: LoopMemory) -> str | None:
    """Run a local strategy-approval committee on the current best and return its verdict as
    guidance text for the next design. Best-effort: any failure returns None (loop unaffected)."""
    best = memory.best
    if best is None or best.scored is None:
        return None
    s = best.scored
    e = best.experiment
    failed = [k for k, v in s.checks.items() if not v]
    mandate = (
        "You are a quantitative strategy approval board reviewing a SYSTEMATIC cross-sectional "
        "factor strategy (not a single stock). Candidate:\n"
        f"- factors: {' + '.join(e.factors)} | mode: {e.mode} | universe: {e.universe} | "
        f"top_pct {e.top_pct} | gross {e.gross} | rebalance {e.rebalance} | timing {e.timing}\n"
        f"- metrics: Sharpe {s.metrics.get('sharpe')}, Calmar {s.metrics.get('calmar')}, "
        f"maxDD {s.metrics.get('max_drawdown')}, ann turnover {s.metrics.get('ann_turnover')}, "
        f"OOS/IS {s.oos_sharpe_ratio}\n"
        f"- failed scorecard checks: {failed or 'none'}\n\n"
        "Decide deploy/refine/reject. Put the SINGLE most impactful change to raise risk-adjusted "
        "return toward Sharpe 2 (switch/blend a factor, add a trend/regime timing overlay, lower "
        "turnover via a tighter quantile or longer rebalance, flip long-only/long-short, reduce "
        "gross) in key_risks. Be specific and actionable."
    )
    try:
        from app.routers.committee import PRESETS
        from app.services import committee_local as cl
        from app.services.agent_nodes import _parse_verdict

        result = cl.deliberate(deps.llm, deps.model, "Investment Committee",
                               PRESETS[0]["members"], mandate).get("result", "")
        verdict = _parse_verdict(result)
        if not verdict:
            return None
        risks = verdict.get("key_risks") or []
        risk_txt = "; ".join(risks) if isinstance(risks, list) else str(risks)
        return f"committee verdict {verdict.get('recommendation', '?')}: {risk_txt}".strip()
    except Exception:  # noqa: BLE001 - committee guidance is advisory; never break the loop
        return None


def _build_portfolio(deps: RLDeps, best: IterationRecord) -> dict:
    """Persist the winning strategy's latest-rebalance book as a named rebalancing portfolio and
    value it into shares — reuses `portfolio_book` (the same flow as backtest_agent._portfolio_run).
    'Rebalancing' = the cadence is recorded; the saved book is a snapshot (no live auto-rebalancer)."""
    from app.services import portfolio_book as pb

    e = best.experiment
    payload = best.result_payload or {}
    top = payload.get("top_weights") or []
    if not top:
        raise ValueError("winning backtest produced no holdings to build a portfolio from")
    universe = get_universe(e.universe)
    amap = {ins.id.upper(): ins.asset_class.value for ins in universe.instruments}
    raw_alloc = [
        {"symbol": w["symbol"], "asset": amap.get(str(w["symbol"]).upper(), "equity"),
         "weight": w["weight"] / 100.0}
        for w in top
    ]
    norm = pb.normalize(raw_alloc, e.mode)
    allocations = norm["allocations"]
    sharpe = (best.scored.metrics.get("sharpe") if best.scored else None)
    name = f"Research · {' + '.join(e.factors)} · {e.universe} · {e.mode}"
    pb.save_portfolio(deps.store, name, {
        "description": f"Auto-built by the research loop from {' + '.join(e.factors)} on {e.universe}",
        "mode": e.mode, "allocations": allocations,
        "tags": ["research", "committee", f"rebalance:{e.rebalance}", f"timing:{e.timing}"],
        "notes": (f"Winner of a committee-guided research loop. Rebalance {e.rebalance}; "
                  f"timing {e.timing}; in-sample Sharpe {sharpe}."),
    })
    valuation = pb.allocate(deps.dm, allocations, 1_000_000.0)
    return {
        "name": name, "mode": e.mode, "rebalance": e.rebalance, "timing": e.timing,
        "allocations": allocations, "exposures": norm["exposures"], "valuation": valuation,
    }


def _scored_frame(s: Scored) -> dict:
    return {
        "metrics": s.metrics, "passed": s.passed, "checks": s.checks,
        "n_checks_passed": s.n_checks_passed, "oos_sharpe": s.oos_sharpe,
        "oos_sharpe_ratio": s.oos_sharpe_ratio, "notes": s.notes,
    }


def _persist_blob(rec: IterationRecord, payload: dict | None) -> dict:
    """Full per-iteration record for the store (carries the dashboard payload for replay)."""
    return {
        "i": rec.i,
        "experiment": asdict(rec.experiment),
        "scored": _scored_frame(rec.scored) if rec.scored else None,
        "objective": rec.objective if rec.objective != float("-inf") else None,
        "error": rec.error,
        "result_payload": payload,
    }
