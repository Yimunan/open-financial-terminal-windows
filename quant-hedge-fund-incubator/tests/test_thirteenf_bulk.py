"""Bulk SEC 13F Data Set parsing — value-unit cutover + long-equity filtering, fully offline.

Builds a tiny in-memory data-set ZIP (SUBMISSION/COVERPAGE/INFOTABLE TSVs) in the client's cache
dir so ``fetch`` is a cache hit (no network), then exercises ``holdings`` and ``rank``.
"""

from __future__ import annotations

import zipfile

import pytest

from qhfi.data.providers.thirteenf_bulk import ThirteenFBulkClient

_SUBMISSION = (
    "ACCESSION_NUMBER\tCIK\tSUBMISSIONTYPE\tFILING_DATE\tPERIODOFREPORT\n"
    "a1\t111\t13F-HR\t15-MAY-2026\t31-MAR-2026\n"      # modern → dollars
    "b1\t222\t13F-HR\t15-MAY-2022\t31-MAR-2022\n"      # pre-2023 → thousands
    "n1\t333\t13F-NT\t15-MAY-2026\t31-MAR-2026\n"      # notice, no holdings → excluded
)
_COVERPAGE = (
    "ACCESSION_NUMBER\tFILINGMANAGER_NAME\n"
    "a1\tAlpha Capital\nb1\tBeta Advisors\nn1\tGamma Notice\n"
)
_INFOTABLE = (
    "ACCESSION_NUMBER\tCUSIP\tNAMEOFISSUER\tVALUE\tSSHPRNAMT\tSSHPRNAMTTYPE\tPUTCALL\n"
    "a1\tC0001\tAAA INC\t1000000\t10000\tSH\t\n"        # $1,000,000 (dollars)
    "a1\tC0002\tBBB INC\t500000\t5000\tSH\tCall\n"      # option → dropped
    "b1\tC0003\tCCC INC\t2000\t100\tSH\t\n"             # 2000 thousands → $2,000,000
)


def _make_dataset(cache_dir, name="2026q2"):
    path = cache_dir / f"{name}_form13f.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("SUBMISSION.tsv", _SUBMISSION)
        zf.writestr("COVERPAGE.tsv", _COVERPAGE)
        zf.writestr("INFOTABLE.tsv", _INFOTABLE)
    return name


def test_holdings_units_and_equity_filter(tmp_path):
    name = _make_dataset(tmp_path)
    h = ThirteenFBulkClient(tmp_path).holdings(name)
    assert set(h["cik"]) == {111, 222}                              # 13F-NT notice excluded
    assert set(h["cusip"]) == {"C0001", "C0003"}                    # Call row dropped
    aaa = h[h["cusip"] == "C0001"].iloc[0]
    ccc = h[h["cusip"] == "C0003"].iloc[0]
    assert aaa["value_usd"] == pytest.approx(1_000_000)             # modern filing: dollars ×1
    assert ccc["value_usd"] == pytest.approx(2_000_000)             # pre-2023: thousands ×1000
    assert aaa["period"] == "2026-03-31" and aaa["filed"] == "2026-05-15"


def test_rank_filters_to_period_and_sorts(tmp_path):
    name = _make_dataset(tmp_path)
    top = ThirteenFBulkClient(tmp_path).rank(name, period="2026-03-31", top=10)
    assert list(top["cik"]) == [111]                                # only the 2026-Q1 filer
    assert top["manager"].iloc[0] == "Alpha Capital"
    assert top["positions"].iloc[0] == 1                            # the Call row was excluded
    assert top["value_usd_bn"].iloc[0] == pytest.approx(0.001)


def test_holdings_cik_filter(tmp_path):
    name = _make_dataset(tmp_path)
    h = ThirteenFBulkClient(tmp_path).holdings(name, ciks={222})
    assert set(h["cik"]) == {222}
