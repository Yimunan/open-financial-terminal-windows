"""Tests for the seeded demo algos (the Algo Trading module's ready-to-run examples).

Two layers, both offline against the local data lake the terminal already caches:
  1. `seed_demo_algos` is idempotent + non-clobbering on a temp store (no duplicates on restart,
     no overwrite of a user edit under the same id).
  2. Each seeded config actually produces a live signal + an order plan through the real
     `AlgoRunner.preview` (template emits a single AAPL target; xsection emits a Dow30 book).

Run: `cd backend && pytest tests/test_algo_demo.py -v`  (needs the cached dow30/AAPL data).
"""

from __future__ import annotations

import pytest

from app.services.agent_demo import DEMO_ALGOS, seed_demo_algos
from app.store import TerminalStore


@pytest.fixture
def store(tmp_path):
    s = TerminalStore(tmp_path / "term.db")
    s.init()
    return s


def test_seed_is_idempotent_and_non_clobbering(store):
    # both demos land, are disarmed, and target the safe sim sandbox book
    seed_demo_algos(store)
    algos = {a["id"]: a for a in store.list_algos()}
    assert set(algos) == set(DEMO_ALGOS)
    assert len(algos) == 2
    for a in algos.values():
        assert a["armed"] is False
        assert a["book"] == "sim"
        assert a["last_run"] is None

    # a user edit under a demo id must NOT be clobbered by a re-seed (restart)
    edited = {**algos["demo-aapl-sma"], "name": "My edited algo", "armed": True}
    edited.pop("id", None)
    edited.pop("updated", None)
    store.save_algo("demo-aapl-sma", edited)

    seed_demo_algos(store)  # second run (simulated restart)
    after = {a["id"]: a for a in store.list_algos()}
    assert len(after) == 2  # no duplicates
    assert after["demo-aapl-sma"]["name"] == "My edited algo"  # edit preserved
    assert after["demo-aapl-sma"]["armed"] is True


def test_demo_kinds_present():
    kinds = {rec["kind"] for rec in DEMO_ALGOS.values()}
    assert kinds == {"template", "xsection"}


def test_template_preview_emits_signal():
    from app.services.algo_runner import AlgoRunner

    out = AlgoRunner().preview({**DEMO_ALGOS["demo-aapl-sma"], "last_run": None})
    assert out["status"] == "preview"
    assert out["signal"]["kind"] == "template"
    assert out["signal"]["symbol"] == "AAPL"
    assert out["signal"]["signal"] in (-1, 0, 1)
    # orders is the (possibly empty) diff to reach the target weight — shape check, not count
    assert isinstance(out["orders"], list)


def test_xsection_preview_emits_book():
    from app.services.algo_runner import AlgoRunner

    out = AlgoRunner().preview({**DEMO_ALGOS["demo-dow30-momentum"], "last_run": None})
    assert out["status"] == "preview"
    assert out["signal"]["kind"] == "xsection"
    assert out["signal"]["universe"] == "dow30"
    assert out["signal"]["factor"] == "momentum"
    assert out["signal"]["n_names"] >= 1
    assert isinstance(out["orders"], list)
