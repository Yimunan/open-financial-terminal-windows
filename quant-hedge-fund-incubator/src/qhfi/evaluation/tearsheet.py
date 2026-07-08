"""Rich HTML/PNG tearsheets via quantstats, written to the reports dir."""

from __future__ import annotations

from pathlib import Path

from qhfi.backtest.engine import BacktestResult


def render(result: BacktestResult, out_dir: Path, title: str) -> Path:
    """quantstats.reports.html(result.returns, output=...) → path to the report."""
    raise NotImplementedError("TODO: quantstats.reports.html into out_dir/<title>.html")
