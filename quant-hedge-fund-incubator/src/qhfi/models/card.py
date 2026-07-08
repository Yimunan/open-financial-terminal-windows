# NOTE: Phase-2 real implementation of the model-card layer (the Phase-1 permissive stub
# let the terminal boot but dropped every save). Cards are plain dataclasses serialized to
# ``card.json`` next to their pickled artifact by ``repository.ModelRepository``.

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ModelStage(str, Enum):
    DRAFT = "draft"
    BACKTEST = "backtest"
    PAPER = "paper"
    PRODUCTION = "production"
    ARCHIVED = "archived"
    # Strict on purpose: ``ModelStage("bogus")`` must raise so promote() can 400 a bad
    # stage. Unknown strings in on-disk manifests are tolerated at parse time instead
    # (ModelCard.from_dict falls back to DRAFT).


class ModelDomain(str, Enum):
    ALLOCATION = "allocation"
    CURVE = "curve"
    RISK = "risk"
    # Strict like ModelStage: a silent _missing_ fallback would defeat _enum_or_none's
    # unknown→None manifest parsing and mislabel unknown domains as RISK.


def _enum_or_none(enum_cls: type, value: Any):
    """Parse an enum from a manifest value; unknown/absent → None (never raises)."""
    if value in (None, ""):
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


@dataclass
class ModelCard:
    """One trained-model version's manifest — everything but the pickled artifact."""

    name: str
    version: int
    stage: ModelStage = ModelStage.DRAFT
    framework: str | None = None
    domain: ModelDomain | None = None
    asset_class: Any | None = None  # qhfi.core.types.AssetClass (imported lazily to stay a leaf)
    created_at: str = ""
    metrics: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    features: list = field(default_factory=list)
    train_span: tuple[str, str] | None = None
    lineage: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    path: str = ""  # artifact directory on disk; set by the repository, not persisted callers' concern

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stage"] = self.stage.value
        d["domain"] = self.domain.value if self.domain else None
        d["asset_class"] = getattr(self.asset_class, "value", self.asset_class)
        d["train_span"] = list(self.train_span) if self.train_span else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModelCard":
        from qhfi.core.types import AssetClass  # leaf import; avoids a hard dep at module load

        stage = _enum_or_none(ModelStage, d.get("stage")) or ModelStage.DRAFT
        span = d.get("train_span")
        return cls(
            name=str(d.get("name", "")),
            version=int(d.get("version", 0)),
            stage=stage,
            framework=d.get("framework"),
            domain=_enum_or_none(ModelDomain, d.get("domain")),
            asset_class=_enum_or_none(AssetClass, d.get("asset_class")),
            created_at=str(d.get("created_at", "")),
            metrics=dict(d.get("metrics") or {}),
            params=dict(d.get("params") or {}),
            features=list(d.get("features") or []),
            train_span=(str(span[0]), str(span[1])) if span and len(span) == 2 else None,
            lineage=dict(d.get("lineage") or {}),
            tags=list(d.get("tags") or []),
            path=str(d.get("path", "")),
        )
