# NOTE: Phase-2 real implementation of the versioned model registry (the Phase-1
# permissive stub silently dropped saves, so the terminal's Trained Models tab was
# always empty). Layout: <root>/<name>/v<version>/{card.json, model.pkl}.

from __future__ import annotations

import json
import pickle
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .card import ModelCard, ModelDomain, ModelStage  # noqa: F401 - ModelStage re-exported

_VDIR = re.compile(r"^v(\d+)$")

# Guards read-modify-write cycles (save's version bump, promote's archive scan) against the
# backend threadpool running two requests at once. Class-wide because consumers construct a
# fresh ModelRepository per request; a desktop app has no cross-process writers to worry about.
_LOCK = threading.RLock()


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via tmp + rename so a crash mid-write never corrupts an existing file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _isdir(p: Path) -> bool:
    """Path.is_dir() that treats *any* OSError as False — pathlib only swallows ENOENT-family
    errors, so an over-long name (ENAMETOOLONG) would otherwise raise out of a mere probe."""
    try:
        return p.is_dir()
    except OSError:
        return False


def _isfile(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


class ModelRepository:
    """Versioned trained-model store: pickled artifact + JSON card per (name, version).

    ``save`` auto-increments the version; promoting to PRODUCTION (via ``save`` or
    ``promote``) archives the incumbent so at most one version of a name is live.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ── paths ────────────────────────────────────────────────────────────────────
    def _resolve_name(self, name: str) -> str:
        """Reuse an existing directory's spelling when it differs only by case — APFS is
        case-insensitive, so 'Demo' and 'demo' share one dir; without this, saves would merge
        on disk while cards/listings split by the caller's spelling. Always scan: a bare
        exists-check can't reveal the on-disk casing on a case-insensitive filesystem."""
        if _isdir(self.root):
            low = name.lower()
            for p in self.root.iterdir():
                if _isdir(p) and p.name.lower() == low:
                    return p.name
        return name

    def _model_dir(self, name: str) -> Path:
        return self.root / self._resolve_name(name)

    def _version_dir(self, name: str, version: int) -> Path:
        return self._model_dir(name) / f"v{int(version)}"

    def _versions(self, name: str) -> list[int]:
        d = self._model_dir(name)
        if not _isdir(d):
            return []
        # set(): a non-canonical dir like v01 aliases v1 — count the version once.
        return sorted({int(m.group(1)) for p in d.iterdir() if p.is_dir() and (m := _VDIR.match(p.name))})

    # ── write ────────────────────────────────────────────────────────────────────
    def save(
        self,
        name: str,
        obj: Any,
        *,
        framework: str | None = None,
        domain: ModelDomain | str | None = None,
        asset_class: Any | None = None,
        params: dict | None = None,
        features: list | None = None,
        train_span: tuple | None = None,
        metrics: dict | None = None,
        lineage: dict | None = None,
        tags: list | None = None,
        stage: ModelStage | str = ModelStage.DRAFT,
    ) -> ModelCard:
        """Pickle ``obj`` as the next version of ``name`` and write its card. Returns the card.

        Everything is serialized *before* any directory is created, so a bad object or a bad
        enum value raises cleanly instead of leaving an empty version dir that would poison
        ``latest``/``load``/``promote`` for the name.
        """
        name = str(name).strip()
        if not name or "/" in name or name.startswith("."):
            raise ValueError(f"bad model name '{name}'")
        stage = ModelStage(stage)  # raises ValueError on a bad stage string
        if isinstance(domain, str) and domain:
            domain = ModelDomain(domain)
        if isinstance(asset_class, str) and asset_class:
            from qhfi.core.types import AssetClass

            asset_class = AssetClass(asset_class)
        blob = pickle.dumps(obj)  # may raise PicklingError — before mkdir on purpose
        with _LOCK:
            name = self._resolve_name(name)
            versions = self._versions(name)
            version = (versions[-1] + 1) if versions else 1
            card = ModelCard(
                name=name,
                version=version,
                stage=stage,
                framework=framework,
                domain=domain,
                asset_class=asset_class,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                metrics=dict(metrics or {}),
                params=dict(params or {}),
                features=list(features or []),
                train_span=(str(train_span[0]), str(train_span[1])) if train_span else None,
                lineage=dict(lineage or {}),
                tags=list(tags or []),
                path="",
            )
            # default=str keeps exotic param values (enums, dates, specs) from breaking the manifest
            payload = json.dumps(card.to_dict(), indent=2, default=str).encode("utf-8")
            vdir = self._version_dir(name, version)
            try:
                vdir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ValueError(f"cannot create '{vdir}': {e}") from None
            try:
                _atomic_write(vdir / "model.pkl", blob)
                _atomic_write(vdir / "card.json", payload)
            except BaseException:
                shutil.rmtree(vdir, ignore_errors=True)  # never leave a half-written version
                raise
            card.path = str(vdir)
            if stage is ModelStage.PRODUCTION:
                self._archive_incumbents(name, keep_version=version)
        return card

    def _write_card(self, card: ModelCard) -> None:
        vdir = self._version_dir(card.name, card.version)
        _atomic_write(vdir / "card.json", json.dumps(card.to_dict(), indent=2, default=str).encode("utf-8"))

    def _archive_incumbents(self, name: str, keep_version: int) -> None:
        """Demote any other PRODUCTION version of ``name`` to ARCHIVED. Tolerant scan: an
        unreadable sibling is skipped, never fatal — one garbled manifest must not abort a
        promotion half-way (incumbent archived, target never promoted)."""
        for v in self._versions(name):
            if v == int(keep_version):
                continue
            try:
                other = self.card(name, v)
            except Exception:  # noqa: BLE001 - garbled/partial sibling: skip it
                continue
            if other.stage is ModelStage.PRODUCTION:
                other.stage = ModelStage.ARCHIVED
                self._write_card(other)

    # ── read ─────────────────────────────────────────────────────────────────────
    def card(self, name: str, version: int) -> ModelCard:
        p = self._version_dir(name, version) / "card.json"
        if not _isfile(p):
            raise ValueError(f"no model '{name}' v{version} in {self.root}")
        try:
            d = json.loads(p.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(f"garbled card for '{name}' v{version}: {e}") from None
        if not isinstance(d, dict):
            raise ValueError(f"garbled card for '{name}' v{version}: not an object")
        c = ModelCard.from_dict(d)
        # Directory identity wins over whatever the manifest claims (a hand-copied version
        # dir must not let promote() mutate some other version's card).
        c.name, c.version, c.path = self._resolve_name(name), int(version), str(p.parent)
        return c

    def cards(self) -> list[ModelCard]:
        """Every (name, version) card in the repository, sorted by (name, version).

        Tolerant of clutter: non-repo files/dirs and unparsable manifests are skipped,
        never fatal — a garbled version must not blank the whole Trained Models tab.
        """
        out: list[ModelCard] = []
        if not _isdir(self.root):
            return out
        for mdir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for v in self._versions(mdir.name):
                try:
                    out.append(self.card(mdir.name, v))
                except Exception:  # noqa: BLE001 - skip a garbled manifest, keep the rest
                    continue
        return out

    def latest(self, name: str) -> ModelCard:
        for v in reversed(self._versions(name)):
            try:
                return self.card(name, v)
            except ValueError:
                continue  # empty/garbled version dir: fall back to the previous one
        raise ValueError(f"no model '{name}' in {self.root}")

    def load(self, name: str, version: int | None = None) -> Any:
        """Unpickle a version's artifact (latest readable version when ``version`` is None)."""
        if version is None:
            version = self.latest(name).version
        p = self._version_dir(name, int(version)) / "model.pkl"
        if not _isfile(p):
            raise ValueError(f"no artifact for '{name}' v{version} in {self.root}")
        return pickle.loads(p.read_bytes())

    # ── lifecycle ────────────────────────────────────────────────────────────────
    def promote(self, name: str, version: int, stage: ModelStage | str) -> ModelCard:
        """Move one version to ``stage``; promoting to PRODUCTION archives the incumbent."""
        stage = ModelStage(stage)  # raises ValueError on a bad stage string
        with _LOCK:
            card = self.card(name, int(version))  # raises ValueError when missing/garbled
            if stage is ModelStage.PRODUCTION and not _isfile(Path(card.path) / "model.pkl"):
                raise ValueError(f"'{name}' v{version} has no artifact — cannot promote to production")
            card.stage = stage
            self._write_card(card)
            if stage is ModelStage.PRODUCTION:
                self._archive_incumbents(card.name, keep_version=card.version)
        return card
