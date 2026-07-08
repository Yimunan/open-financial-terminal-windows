"""Model repository: versioning, card persistence, load-by-selector, and the single-
production promotion rule. Offline; uses a tmp root and plain-Python 'models'."""

from __future__ import annotations

import pytest

from qhfi.models import ModelCard, ModelRepository, ModelStage


class _Toy:
    """A stand-in trained model (no ML deps needed — must pickle round-trip)."""

    def __init__(self, weights):
        self.weights = weights

    def predict(self, x):
        return sum(w * v for w, v in zip(self.weights, x))


def test_versioning_and_artifact_roundtrip(tmp_path):
    repo = ModelRepository(tmp_path)
    repo.save("alpha", _Toy([1, 2]), framework="custom", metrics={"ic": 0.05},
              features=["mom", "val"], train_span=("2010-01-01", "2020-01-01"))
    card2 = repo.save("alpha", _Toy([3, 4]), metrics={"ic": 0.07})

    assert card2.version == 2 and repo.latest("alpha") == 2
    model, card = repo.load("alpha", "latest")
    assert model.predict([1, 1]) == 7                      # weights [3,4] survived pickle
    assert card.metrics["ic"] == 0.07
    # v1 still retrievable + its metadata intact
    m1, c1 = repo.load("alpha", 1)
    assert m1.weights == [1, 2] and c1.features == ["mom", "val"]
    assert c1.created_at is not None


def test_single_production_promotion(tmp_path):
    repo = ModelRepository(tmp_path)
    repo.save("alpha", _Toy([1]))
    repo.save("alpha", _Toy([2]))

    repo.promote("alpha", 1, ModelStage.PRODUCTION)
    assert repo.card("alpha", 1).stage is ModelStage.PRODUCTION
    prod_model, prod_card = repo.production("alpha")
    assert prod_card.version == 1

    # promoting v2 to production archives v1
    repo.promote("alpha", 2, ModelStage.PRODUCTION)
    assert repo.card("alpha", 1).stage is ModelStage.ARCHIVED
    assert repo.production("alpha")[1].version == 2


def test_load_by_stage_and_listing(tmp_path):
    repo = ModelRepository(tmp_path)
    repo.save("a", _Toy([1]))
    repo.save("b", _Toy([2]))
    repo.promote("b", 1, ModelStage.STAGING)

    assert {c.name for c in repo.cards()} == {"a", "b"}
    _, staged = repo.load("b", "staging")
    assert staged.version == 1
    with pytest.raises(KeyError):
        repo.load("a", "production")                        # none promoted


def test_card_is_serializable():
    c = ModelCard(name="x", version=1)
    assert ModelCard.model_validate_json(c.model_dump_json()).name == "x"
