"""Typed settings.

Two sources, deliberately separate:

* **Secrets / endpoints / paths** come from the environment / ``.env`` via the pydantic
  ``Settings`` below (``QHFI_*`` prefix). These are deployment-specific and not in git.
* **Incubator-wide knobs** (execution, construction, promotion thresholds) come from the
  versioned, reviewable ``config/settings.yaml`` via the typed loaders below — so the bar can
  be tuned without code edits, mirroring how universes are versioned in ``core/universe_io``.

Until these loaders existed, the ``backtest:``/``scorecard:`` blocks in settings.yaml were
documentation only (nothing read them); the dataclass defaults in code were the real source
of truth. The loaders close that gap.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:  # avoid import cost / cycles at module load; resolved lazily in the loaders
    from qhfi.backtest.engine import ExecutionConfig
    from qhfi.evaluation.scorecard import Thresholds
    from qhfi.portfolio.construction import ConstructionConfig

SETTINGS_YAML = Path("config/settings.yaml")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QHFI_", env_file=".env", extra="ignore")

    # Local LLM stack
    llm_base_url: str = "http://localhost:8001/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "gemma"
    langgraph_url: str = "http://localhost:8082"
    crewai_url: str = "http://localhost:8083"

    # Paths
    data_dir: Path = Path("./data")
    reports_dir: Path = Path("./reports")
    models_dir: Path = Path("./models")
    registry_db: Path = Path("./registry.sqlite")


def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=8)
def load_yaml_settings(path: str | Path = SETTINGS_YAML) -> dict[str, Any]:
    """Parse ``config/settings.yaml`` into a plain dict (cached per path)."""
    return yaml.safe_load(Path(path).read_text()) or {}


def _keep(cls: type, block: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys that are fields of dataclass ``cls`` — tolerate doc-only/extra yaml keys."""
    fields = getattr(cls, "__dataclass_fields__", {})
    return {k: v for k, v in block.items() if k in fields}


def backtest_execution_config(path: str | Path = SETTINGS_YAML) -> ExecutionConfig:
    """``ExecutionConfig`` from the ``backtest:`` block — the no-trade band lives here."""
    from qhfi.backtest.engine import ExecutionConfig
    from qhfi.backtest.fills import FillTiming

    block = dict(load_yaml_settings(path).get("backtest", {}))
    kept = _keep(ExecutionConfig, block)
    if "fill" in kept:
        kept["fill"] = FillTiming(kept["fill"])
    return ExecutionConfig(**kept)


def construction_config(path: str | Path = SETTINGS_YAML) -> ConstructionConfig:
    """``ConstructionConfig`` from the ``construction:`` block — score smoothing lives here."""
    from qhfi.portfolio.construction import ConstructionConfig

    block = load_yaml_settings(path).get("construction", {})
    return ConstructionConfig(**_keep(ConstructionConfig, block))


def scorecard_thresholds(path: str | Path = SETTINGS_YAML) -> Thresholds:
    """``Thresholds`` from the ``scorecard:`` block — the promotion gate."""
    from qhfi.evaluation.scorecard import Thresholds

    block = load_yaml_settings(path).get("scorecard", {})
    return Thresholds(**_keep(Thresholds, block))
