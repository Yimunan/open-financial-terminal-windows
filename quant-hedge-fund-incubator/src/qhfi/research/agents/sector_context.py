"""Per-sector context engineering for the equity research agent.

A single generic prompt under-serves every sector: technology returns are driven by secular
growth and real-rate duration, utilities trade as rate-sensitive bond proxies, energy tracks
the commodity cycle. This module holds a curated ``SectorProfile`` per GICS sector — the
analyst lens, the real return drivers, the macro sensitivities, how factors typically behave,
and the characteristic risks — and renders it into (a) a sector-specialist system prompt and
(b) a compact context block prepended to the factor evidence.

This is *qualitative* grounding only. The deterministic quant composite stays equal-weighted
(the codebase's honest default); the model is handed each factor's measured within-sector IC
and is told how factors usually behave here — it is never given hand-tuned factor weights.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectorProfile:
    lens: str                  # one-line analyst persona / what dominates returns here
    drivers: list[str]         # real return drivers
    macro: list[str]           # macro sensitivities (rates, oil, USD, credit, …)
    factor_note: str           # how momentum/low-vol/reversal/value/quality typically behave
    risks: list[str]           # characteristic risks to weigh


SECTOR_PROFILES: dict[str, SectorProfile] = {
    "Information Technology": SectorProfile(
        lens="secular-growth and earnings-revision driven; high-multiple names are long-duration "
             "and fall when real yields rise; momentum and quality usually dominate.",
        drivers=["secular demand (cloud, AI, the semiconductor cycle)", "earnings revisions & guidance",
                 "hyperscaler/enterprise capex", "real interest rates (duration on high-multiple names)"],
        macro=["real 10y yield", "USD (overseas revenue)", "semiconductor inventory cycle"],
        factor_note="Momentum and quality tend to lead; low-volatility lags in risk-on rallies; "
                    "cheap value is often a trap (structurally challenged names).",
        risks=["multiple compression on rising real yields", "mega-cap concentration",
               "semis inventory swings", "antitrust/regulation"],
    ),
    "Financials": SectorProfile(
        lens="rate- and credit-cycle driven; anchored to book value; sensitive to the yield-curve "
             "slope and net interest margins.",
        drivers=["yield-curve slope / NIM", "credit cycle & loan losses", "capital-markets activity",
                 "regulation & capital ratios"],
        macro=["2s10s curve", "credit spreads", "Fed policy path", "unemployment (credit losses)"],
        factor_note="Value and momentum both useful; quality matters for avoiding credit blowups; "
                    "watch price-to-book versus realized losses.",
        risks=["curve inversion compressing margins", "credit deterioration in recession",
               "deposit/funding stress", "regulatory capital changes"],
    ),
    "Energy": SectorProfile(
        lens="commodity-price driven (crude & natural gas); capital discipline and free-cash-flow "
             "yield matter more than growth.",
        drivers=["oil & gas prices", "capex discipline / shareholder returns", "production volumes",
                 "refining crack spreads"],
        macro=["WTI/Brent", "USD", "OPEC+ supply policy", "global demand (China)"],
        factor_note="Value and price-momentum lead; low-volatility is weak; quality reads as "
                    "balance-sheet strength.",
        risks=["commodity price collapse", "demand destruction in recession",
               "geopolitical supply shocks", "energy-transition stranded assets"],
    ),
    "Utilities": SectorProfile(
        lens="defensive bond-proxy; valuations move inverse to rates; regulated returns and "
             "dividend stability dominate. Low beta.",
        drivers=["interest rates (inverse — bond proxy)", "regulated ROE / rate cases",
                 "dividend yield & coverage", "load growth / electrification"],
        macro=["10y yield (inverse)", "inflation (rate base)", "power demand"],
        factor_note="Low-volatility and quality lead; momentum is weak; value shows up as dividend "
                    "yield.",
        risks=["rising rates pressuring valuations", "regulatory disallowances",
               "rate-base capex financing", "weather/demand variability"],
    ),
    "Health Care": SectorProfile(
        lens="defensive at the sector level but high idiosyncratic risk; pipeline/patent cliffs and "
             "drug-pricing policy dominate single names.",
        drivers=["drug pipeline / FDA outcomes", "patent cliffs & generic erosion",
                 "drug-pricing policy", "aging demographics"],
        macro=["policy/election risk", "defensive rotation in risk-off"],
        factor_note="Quality and low-volatility lead in the defensive sub-industries; momentum works "
                    "on pipeline news; value is mixed (biotech is binary).",
        risks=["binary clinical/FDA events", "drug-pricing legislation", "patent expirations",
               "litigation"],
    ),
    "Consumer Staples": SectorProfile(
        lens="defensive, low-beta; pricing power and input-cost pass-through drive margins; "
             "bond-proxy-like.",
        drivers=["pricing power vs input costs", "volume/elasticity", "FX (multinationals)",
                 "agricultural input costs"],
        macro=["USD", "input commodities (ags)", "defensive rotation"],
        factor_note="Low-volatility and quality lead; momentum is weak; value via stable cash flows.",
        risks=["margin squeeze from input inflation", "private-label competition", "FX translation",
               "shifting consumer preferences"],
    ),
    "Consumer Discretionary": SectorProfile(
        lens="cyclical; consumer spending and rate-sensitive big-ticket/financing demand; wide "
             "dispersion between e-commerce and brick-and-mortar.",
        drivers=["consumer spending / confidence", "rates (financing, housing)",
                 "labor market / wages", "e-commerce share shift"],
        macro=["consumer confidence", "rates", "unemployment", "gasoline prices"],
        factor_note="Momentum and quality lead; low-volatility is weak in this cyclical sector; cheap "
                    "value can be a trap for disrupted retail.",
        risks=["consumer slowdown / recession", "rate-driven demand hit", "inventory gluts",
               "secular retail disruption"],
    ),
    "Industrials": SectorProfile(
        lens="cyclical, capex- and PMI-driven, often early-cycle leadership; broad (machinery, "
             "aerospace, transports, defense).",
        drivers=["ISM/PMI manufacturing cycle", "capex & infrastructure spend", "transport volumes",
                 "defense budgets"],
        macro=["ISM PMI", "global growth", "oil (transports/airlines)", "fiscal/infrastructure"],
        factor_note="Momentum and value both lead at cycle turns; quality guards the balance sheet; "
                    "low-volatility lags.",
        risks=["cyclical downturn / PMI rollover", "supply-chain & input costs",
               "fuel costs (transports)", "trade/tariff exposure"],
    ),
    "Materials": SectorProfile(
        lens="commodity-cycle and global-demand driven (especially China); USD-sensitive; "
             "chemicals, metals & mining.",
        drivers=["industrial commodity prices", "China demand / global growth", "USD (inverse)",
                 "chemical spreads"],
        macro=["China PMI", "USD", "metals & bulk commodity prices", "global growth"],
        factor_note="Momentum and value lead; low-volatility is weak; quality is balance-sheet "
                    "survival through the cycle.",
        risks=["commodity downturn", "China demand slowdown", "USD strength", "energy/input costs"],
    ),
    "Real Estate": SectorProfile(
        lens="rate-sensitive bond-proxy (REITs); cap-rate and occupancy driven; large sub-sector "
             "dispersion (office weak, industrial/data-center strong).",
        drivers=["rates / cap rates (inverse)", "occupancy & rent growth",
                 "sub-sector secular trends", "refinancing costs"],
        macro=["10y yield (inverse)", "credit availability", "employment (office/retail demand)"],
        factor_note="Low-volatility and FFO-yield value are relevant; momentum captures sub-sector "
                    "divergence; quality reads as leverage/refi risk.",
        risks=["rising rates compressing valuations & raising refi costs", "office secular decline",
               "credit/financing tightening", "oversupply"],
    ),
    "Communication Services": SectorProfile(
        lens="a barbell — defensive telecom (bond-proxy) plus advertising-cyclical, momentum-driven "
             "media/internet. Treat the two cohorts differently.",
        drivers=["digital advertising cycle", "subscriber/streaming growth",
                 "telecom rates & dividends", "content/IP competition"],
        macro=["ad spend (cyclical)", "rates (telecom)", "consumer spending"],
        factor_note="Momentum/quality lead the internet & media names; low-volatility/value fit "
                    "telecom; do not blend the two cohorts blindly.",
        risks=["ad-spend downturn", "streaming competition / churn",
               "telecom capex & rate sensitivity", "regulatory/antitrust"],
    ),
}

DEFAULT_PROFILE = SectorProfile(
    lens="generalist cross-sectional equity lens; rely primarily on the measured factor evidence.",
    drivers=["cross-sectional factor signals", "earnings momentum", "valuation"],
    macro=["broad market beta", "rates", "risk sentiment"],
    factor_note="Weigh the factors by their measured within-sector IC shown below.",
    risks=["crowding in popular factors", "regime change", "thin breadth / few names"],
)

# Short aliases → canonical GICS key (lower-cased), so non-canonical inputs still resolve.
_ALIASES: dict[str, str] = {
    "tech": "information technology", "technology": "information technology", "it": "information technology",
    "financial": "financials", "financial services": "financials", "banks": "financials",
    "health": "health care", "healthcare": "health care",
    "staples": "consumer staples", "discretionary": "consumer discretionary",
    "telecom": "communication services", "communications": "communication services",
    "reits": "real estate", "utility": "utilities",
}


def profile_for(sector: str) -> SectorProfile:
    """Resolve a sector name (canonical GICS, an alias, or a loose variant) to its profile,
    falling back to ``DEFAULT_PROFILE`` for unknown/unclassified sectors."""
    key = (sector or "").strip().lower()
    key = _ALIASES.get(key, key)
    for canon, prof in SECTOR_PROFILES.items():
        if canon.lower() == key:
            return prof
    for canon, prof in SECTOR_PROFILES.items():  # loose substring fallback
        c = canon.lower()
        if key and (key in c or c in key):
            return prof
    return DEFAULT_PROFILE


def augment_system(base: str, sector: str) -> str:
    """Append a sector-specialist persona to the base system prompt."""
    return f"{base}\n\nYou are acting as a {sector} sector specialist. {profile_for(sector).lens}"


def context_block(sector: str) -> str:
    """A compact, token-light sector context to prepend to the factor evidence."""
    p = profile_for(sector)
    return (
        f"Sector context — {sector}:\n"
        f"  Lens: {p.lens}\n"
        f"  Key return drivers: {'; '.join(p.drivers)}\n"
        f"  Macro sensitivities: {'; '.join(p.macro)}\n"
        f"  Factor behavior: {p.factor_note}\n"
        f"  Characteristic risks: {'; '.join(p.risks)}\n"
        "Use this domain context to shape regime_view and risks, but never override the numeric "
        "factor evidence below, and never invent a ticker."
    )
