"""Offline tests for scripts/build_cusip_crosswalk.py — the script's own logic:
unique_cusips() (CUSIP collection across the 13F lake) and main()'s orchestration
(skip-known resumability, CLI cap, batch-by-_BATCH, upsert persistence). The underlying
stores/mapper are covered in test_crosswalk.py; here a FakeMapper replaces OpenFIGI so the
suite is fully offline/deterministic. main() never calls catalog.refresh() (that lives in
the __main__ guard), so nothing network/doc-related is touched.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from qhfi.data.crosswalk import CusipTickerStore
from qhfi.data.holdings import HoldingsStore


def _load_script():
    p = Path(__file__).resolve().parents[1] / "scripts" / "build_cusip_crosswalk.py"
    spec = importlib.util.spec_from_file_location("build_cusip_crosswalk", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # safe: __main__ guard prevents main()/refresh() running
    return mod


class FakeMapper:
    """Maps anything except CUSIPs starting with 'X' (unmappable). Records every chunk."""

    def __init__(self, *a, **k):
        self.chunks: list[list[str]] = []

    def map(self, cusips):
        self.chunks.append(list(cusips))
        return {c: {"ticker": c[:4], "name": f"N{c}", "exch": "US", "sec_type": "Common Stock"}
                for c in cusips if not c.startswith("X")}


def _seed_holdings(root, cik, period, cusips):
    HoldingsStore(root).save(cik, "Mgr", period, pd.DataFrame({"cusip": cusips}))


def _use_tmp_lake(monkeypatch, mod, tmp_path):
    """Point main()'s lake_root() at tmp_path and swap OpenFIGI for a recording FakeMapper."""
    fake = FakeMapper()
    monkeypatch.setattr(mod, "lake_root", lambda: tmp_path)
    monkeypatch.setattr(mod, "OpenFigiMapper", lambda *a, **k: fake)
    monkeypatch.setattr(mod.sys, "argv", ["build_cusip_crosswalk.py"])
    return fake


# ── unique_cusips() ────────────────────────────────────────────────────────────
def test_unique_cusips_dedups_and_sorts_across_files(tmp_path):
    mod = _load_script()
    _seed_holdings(tmp_path, 111, "2023Q1", ["037833100", "191216100", "037833100"])
    _seed_holdings(tmp_path, 222, "2023Q1", ["191216100", "000000000"])
    assert mod.unique_cusips(HoldingsStore(tmp_path)) == ["000000000", "037833100", "191216100"]


def test_unique_cusips_drops_na(tmp_path):
    mod = _load_script()
    _seed_holdings(tmp_path, 111, "2023Q1", ["037833100", None, np.nan, "191216100"])
    assert mod.unique_cusips(HoldingsStore(tmp_path)) == ["037833100", "191216100"]


def test_unique_cusips_empty_lake(tmp_path):
    mod = _load_script()
    assert mod.unique_cusips(HoldingsStore(tmp_path)) == []


def test_unique_cusips_only_globs_cik_subdir_depth(tmp_path):
    mod = _load_script()
    _seed_holdings(tmp_path, 111, "2023Q1", ["037833100"])
    # stray parquet directly under data_dir (not in a <cik>/ subdir) → must be ignored
    store = HoldingsStore(tmp_path)
    pd.DataFrame({"cusip": ["999999999"]}).to_parquet(store.data_dir / "stray.parquet")
    assert mod.unique_cusips(store) == ["037833100"]


# ── main() ──────────────────────────────────────────────────────────────────────
def test_main_skips_known_cusips(monkeypatch, tmp_path):
    mod = _load_script()
    fake = _use_tmp_lake(monkeypatch, mod, tmp_path)
    _seed_holdings(tmp_path, 111, "2023Q1", ["AAA1", "BBB2", "CCC3"])
    CusipTickerStore(tmp_path).upsert(
        {"AAA1": {"ticker": "AAA", "name": "n", "exch": "US", "sec_type": "Common Stock"}}
    )

    mod.main()

    mapped = [c for chunk in fake.chunks for c in chunk]
    assert sorted(mapped) == ["BBB2", "CCC3"]                       # AAA1 skipped
    assert CusipTickerStore(tmp_path).known() == {"AAA1", "BBB2", "CCC3"}


def test_main_cap_limits_todo(monkeypatch, tmp_path):
    mod = _load_script()
    fake = _use_tmp_lake(monkeypatch, mod, tmp_path)
    _seed_holdings(tmp_path, 111, "2023Q1", [f"C{i:04d}" for i in range(5)])
    monkeypatch.setattr(mod.sys, "argv", ["build_cusip_crosswalk.py", "2"])

    mod.main()

    mapped = [c for chunk in fake.chunks for c in chunk]
    assert len(mapped) == 2
    assert len(CusipTickerStore(tmp_path).load()) == 2


def test_main_batches_by_batch_size(monkeypatch, tmp_path):
    mod = _load_script()
    fake = _use_tmp_lake(monkeypatch, mod, tmp_path)
    cusips = [f"C{i:04d}" for i in range(120)]
    _seed_holdings(tmp_path, 111, "2023Q1", cusips)

    mod.main()

    assert [len(c) for c in fake.chunks] == [50, 50, 20]
    assert all(len(c) <= mod._BATCH for c in fake.chunks)
    assert sorted(c for chunk in fake.chunks for c in chunk) == sorted(cusips)


def test_main_drops_unmappable_end_to_end(monkeypatch, tmp_path):
    mod = _load_script()
    _use_tmp_lake(monkeypatch, mod, tmp_path)
    _seed_holdings(tmp_path, 111, "2023Q1", ["AAA1", "XBAD1", "BBB2", "XBAD2"])

    mod.main()

    persisted = set(CusipTickerStore(tmp_path).load()["cusip"])
    assert persisted == {"AAA1", "BBB2"}                           # X-prefixed dropped


def test_main_prints_summary_counts(monkeypatch, tmp_path, capsys):
    mod = _load_script()
    _use_tmp_lake(monkeypatch, mod, tmp_path)
    _seed_holdings(tmp_path, 111, "2023Q1", ["AAA1", "BBB2", "CCC3"])
    CusipTickerStore(tmp_path).upsert(
        {"AAA1": {"ticker": "AAA", "name": "n", "exch": "US", "sec_type": "Common Stock"}}
    )

    mod.main()

    out = capsys.readouterr().out
    assert "unique CUSIPs in 13F lake: 3" in out
    assert "already mapped: 1" in out
    assert "to map now: 2" in out
