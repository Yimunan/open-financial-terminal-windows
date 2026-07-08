"""SQLite-backed persistence for strategy records, transitions, and backtest results.

Keeps the incubator auditable and reproducible: which idea became which code, what it
scored, when it was promoted, and by whom (human vs which agent). Backtest result blobs
(equity curve, metrics, config) are stored so reports can be regenerated without re-running.
"""

from __future__ import annotations

from pathlib import Path

from qhfi.registry.models import LifecycleState, StrategyRecord


class Registry:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        # TODO: connect sqlite3, create tables (strategies, transitions, backtests) if absent

    def upsert(self, record: StrategyRecord) -> None:
        raise NotImplementedError("TODO: write record + serialized history")

    def get(self, name: str) -> StrategyRecord:
        raise NotImplementedError("TODO: load record by name")

    def list(self, state: LifecycleState | None = None) -> list[StrategyRecord]:
        raise NotImplementedError("TODO: query, optionally filtered by state")

    def save_backtest(self, strategy: str, metrics: dict, meta: dict) -> str:
        """Persist a backtest run; return its id (referenced by lifecycle transitions)."""
        raise NotImplementedError("TODO: insert backtest row, return generated id")
