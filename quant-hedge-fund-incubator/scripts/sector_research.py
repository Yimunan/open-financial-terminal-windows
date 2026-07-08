"""Run the sector equity-research agent over a universe — one LLM-grounded note per GICS
sector (or a single sector if named). Requires the local vLLM proxy warm on :8001.

  .venv\\Scripts\\python.exe scripts\\sector_research.py ["Health Care"]   (default: all sectors)
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from rich.console import Console

from qhfi.core.universe_io import load_universe
from qhfi.data.lake import market_store
from qhfi.factors.market import MarketPanels
from qhfi.research.agents.sector import SectorResearchAgent, render_note

POOL = "config/instruments/equity_sectors.yaml"
ONE = sys.argv[1] if len(sys.argv) > 1 else None


def main() -> None:
    console = Console()
    universe = load_universe(POOL)
    close = MarketPanels.from_store(market_store(), universe).close
    sectors = universe.groups("gics_sector")
    targets = [ONE] if ONE else sorted({s for s in sectors.values() if s != "__none__"})
    console.print(f"Pool: {universe.name} | {close.shape[0]}d × {close.shape[1]} names | "
                  f"{len(targets)} sector(s)\n")

    agent = SectorResearchAgent()
    for sector in targets:
        try:
            note = agent.research(sector, close, universe)
        except Exception as e:  # cold proxy / thin sector — warn and continue the fleet
            console.print(f"[yellow]skip {sector}: {type(e).__name__}: {e}[/yellow]")
            continue
        render_note(note, console)
        console.print()


if __name__ == "__main__":
    main()
