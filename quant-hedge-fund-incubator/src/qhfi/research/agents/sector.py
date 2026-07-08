"""SectorResearchAgent — an LLM equity analyst grounded in quant factor evidence, scoped to
one GICS sector.

The split is deliberate: ``evidence()`` is **pure, deterministic quant** — it ranks the
sector's names by a composite of momentum / low-volatility / short-term-reversal z-scores and
measures each factor's information coefficient within the sector. ``research()`` then hands
*only those numbers* (never raw prices) to the local LLM via ``LLMClient.structured``, so the
model adds judgment — conviction longs/shorts with a per-name thesis, a regime view, risks,
and testable hypotheses — but cannot fabricate the ranking. The numeric composite is merged
back onto each pick after the call, overwriting whatever the model guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel

if TYPE_CHECKING:
    from rich.console import Console

from qhfi.core.types import Panel, Universe
from qhfi.factors import evaluation as fe
from qhfi.factors import transforms as tf
from qhfi.factors.base import Factor
from qhfi.factors.library import MomentumFactor, ShortTermReversalFactor, VolatilityFactor
from qhfi.research.agents.ideation import Hypothesis
from qhfi.research.agents.sector_context import augment_system, context_block
from qhfi.research.client import LLMClient

# Factor key → factor instance. Each is scored via ``.signed()`` so higher always = more long.
_FACTORS: dict[str, Factor] = {
    "momentum": MomentumFactor(),
    "lowvol": VolatilityFactor(),     # direction -1 → higher signed score = lower vol
    "reversal": ShortTermReversalFactor(),
}


# ── structured LLM output ──────────────────────────────────────────────────────
class NamePick(BaseModel):
    ticker: str
    side: Literal["long", "short"]
    thesis: str
    key_driver: str
    composite_score: float | None = None  # filled from quant evidence post-hoc, not by the LLM


class SectorResearchNote(BaseModel):
    sector: str
    regime_view: str
    longs: list[NamePick]
    shorts: list[NamePick]
    risks: list[str]
    hypotheses: list[Hypothesis]


# ── deterministic quant evidence ───────────────────────────────────────────────
@dataclass
class SectorEvidence:
    sector: str
    n_names: int
    n_days: int
    names: list[dict]                       # per name: ticker + composite + factor z-scores, ranked desc
    factor_ic: dict[str, dict[str, float]]  # factor → {mean_ic, ic_ir}

    def composite_by_ticker(self) -> dict[str, float]:
        return {r["ticker"]: r["composite"] for r in self.names}

    def to_prompt(self, top_k: int, n_hypotheses: int) -> str:
        hdr = f"{'ticker':<8}{'composite':>11}" + "".join(f"{k:>11}" for k in _FACTORS)
        rows = [
            f"{r['ticker']:<8}{r['composite']:>11.2f}"
            + "".join(f"{r[k]:>11.2f}" for k in _FACTORS)
            for r in self.names
        ]
        ic = "\n".join(
            f"  {k:<10} mean_IC={v['mean_ic']:+.4f}  IC_IR={v['ic_ir']:+.3f}"
            for k, v in self.factor_ic.items()
        )
        return (
            f"GICS sector: {self.sector}\n"
            f"{self.n_names} names, {self.n_days} trading days. Factor z-scores as of the last day "
            f"(higher = more long; lowvol = low-volatility tilt). Composite = mean of the three.\n\n"
            f"{hdr}\n" + "\n".join(rows) + "\n\n"
            f"Within-sector 5-day factor predictiveness:\n{ic}\n\n"
            f"Pick up to {top_k} conviction longs (highest composite) and up to {top_k} shorts "
            f"(lowest composite) from THESE tickers only, and propose {n_hypotheses} hypotheses."
        )


class SectorResearchAgent:
    SYSTEM = (
        "You are a sector equity analyst. You are given factor evidence (momentum, "
        "low-volatility, short-term reversal, a composite rank, and each factor's information "
        "coefficient) for the stocks in one GICS sector. Rank conviction longs/shorts grounded "
        "ONLY in the evidence provided — never invent a ticker or a number. Explain the key "
        "driver per name, give a sector regime view and the main risks, and propose testable "
        "daily-OHLCV strategy hypotheses. Be specific and skeptical; note where each edge fails."
    )

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()

    def evidence(self, sector: str, prices: Panel, universe: Universe) -> SectorEvidence:
        """Deterministic per-name factor evidence for ``sector`` — no LLM."""
        groups = universe.groups("gics_sector")
        ids = [i for i, s in groups.items() if s == sector and i in prices.columns]
        if len(ids) < 2:
            raise ValueError(f"sector {sector!r} has fewer than 2 names with price data")
        sub = Universe(name=sector, instruments=[universe.by_id(i) for i in ids])
        sub_prices = cast(Panel, prices[ids])

        z = {k: tf.zscore(f.signed(sub_prices, sub)) for k, f in _FACTORS.items()}
        panels = list(z.values())
        composite = panels[0].copy()
        for p in panels[1:]:
            composite = composite.add(p)
        composite = composite / len(panels)

        last = composite.iloc[-1].dropna().sort_values(ascending=False)
        names = [
            {"ticker": t, "composite": float(last[t]),
             **{k: float(z[k].iloc[-1].get(t, float("nan"))) for k in _FACTORS}}
            for t in last.index
        ]
        factor_ic = {}
        for k, f in _FACTORS.items():
            s = fe.ic_summary(fe.information_coefficient(f.signed(sub_prices, sub), sub_prices, horizon=5))
            factor_ic[k] = {"mean_ic": s.mean_ic, "ic_ir": s.ic_ir}

        return SectorEvidence(sector=sector, n_names=len(ids), n_days=len(sub_prices),
                              names=names, factor_ic=factor_ic)

    def research(
        self, sector: str, prices: Panel, universe: Universe, *, top_k: int = 5, n_hypotheses: int = 3
    ) -> SectorResearchNote:
        """Build evidence, ask the LLM for a structured note, merge quant scores back in.

        Context-engineered per sector: a sector-specialist system prompt + a curated context
        block (drivers/macro/factor-behavior/risks) is prepended to the deterministic evidence.
        """
        ev = self.evidence(sector, prices, universe)
        system = augment_system(self.SYSTEM, sector)
        user = f"{context_block(sector)}\n\n{ev.to_prompt(top_k, n_hypotheses)}"
        raw = self.client.structured(system, user, SectorResearchNote.model_json_schema())
        note = SectorResearchNote(**raw)
        note.sector = note.sector or sector
        scores = ev.composite_by_ticker()
        for pick in (*note.longs, *note.shorts):
            pick.composite_score = scores.get(pick.ticker)
        return note


def render_note(note: SectorResearchNote, console: "Console | None" = None) -> None:
    """Pretty-print a SectorResearchNote to the terminal via rich."""
    from rich.console import Console
    from rich.table import Table

    console = console or Console()
    console.rule(f"[bold]{note.sector}[/bold]")
    console.print(f"[italic]Regime view:[/italic] {note.regime_view}\n")

    for label, picks, color in [("Longs", note.longs, "green"), ("Shorts", note.shorts, "red")]:
        if not picks:
            continue
        t = Table(title=f"{label} ({len(picks)})", title_style=f"bold {color}", expand=False)
        t.add_column("ticker", style="bold cyan", no_wrap=True)
        t.add_column("composite", justify="right")
        t.add_column("key driver", no_wrap=True)
        t.add_column("thesis")
        for p in picks:
            score = "-" if p.composite_score is None else f"{p.composite_score:+.2f}"
            t.add_row(p.ticker, score, p.key_driver, p.thesis)
        console.print(t)

    if note.risks:
        console.print("\n[bold]Risks[/bold]")
        for r in note.risks:
            console.print(f"  • {r}")

    if note.hypotheses:
        console.print("\n[bold]Hypotheses[/bold]")
        for h in note.hypotheses:
            console.print(f"  • [bold]{h.title}[/bold] — {h.rationale}")
            console.print(f"    signal: {h.signal_description}")
