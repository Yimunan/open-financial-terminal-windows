"""qhfi ModelRepository link.

Reads the versioned trained-model store at the linked ``models_dir`` (the qhfi
``ModelRepository`` artifact root) and exposes it for the terminal: cards grouped by name
with their versions/stages/metrics, plus stage promotion. Read-only apart from ``promote``
— artifacts are written by qhfi training, not the terminal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qhfi.models import ModelRepository, ModelStage

from app.services import registry as reg


def _repo(store: Any) -> ModelRepository:
    return ModelRepository(root=reg.get_paths(store)["models_dir"])


def _version_row(card: Any) -> dict:
    return {
        "version": card.version,
        "stage": card.stage.value,
        "framework": card.framework,
        "domain": card.domain.value if card.domain else None,
        "asset_class": card.asset_class.value if card.asset_class else None,
        "created_at": card.created_at,
        "metrics": card.metrics or {},
        "features": card.features or [],
        "train_span": list(card.train_span) if card.train_span else None,
        "tags": card.tags or [],
    }


def list_repo_models(store: Any) -> dict:
    """All trained models grouped by name, newest version first per name."""
    repo = _repo(store)
    root = str(repo.root)
    grouped: dict[str, list[dict]] = {}
    try:
        cards = repo.cards()
    except Exception as e:  # noqa: BLE001 - a missing/garbled repo shouldn't 500 the widget
        return {"models": [], "root": root, "exists": False, "error": f"{type(e).__name__}: {e}"}
    for card in cards:
        grouped.setdefault(card.name, []).append(_version_row(card))
    models = []
    for name, versions in sorted(grouped.items()):
        versions.sort(key=lambda v: v["version"], reverse=True)
        prod = next((v["version"] for v in versions if v["stage"] == ModelStage.PRODUCTION.value), None)
        models.append({
            "name": name,
            "versions": versions,
            "latest": versions[0]["version"] if versions else None,
            "production_version": prod,
        })
    return {"models": models, "root": root, "exists": Path(root).exists()}


def promote_model(store: Any, name: str, version: int, stage: str) -> dict:
    """Move a version to a lifecycle stage (promoting to production archives the incumbent)."""
    try:
        st = ModelStage(stage)
    except ValueError:
        raise ValueError(f"bad stage '{stage}' (draft|backtest|paper|production|archived)") from None
    card = _repo(store).promote(name, version, st)
    return _version_row(card) | {"name": card.name}
