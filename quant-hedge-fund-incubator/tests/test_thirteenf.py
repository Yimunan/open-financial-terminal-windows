"""Offline tests for 13F: info-table parser, ThirteenFClient (mocked EDGAR), HoldingsStore."""

from __future__ import annotations

import pandas as pd

from qhfi.data.holdings import HoldingsStore
from qhfi.data.providers.edgar import EdgarClient, Filing
from qhfi.data.providers.thirteenf import ThirteenFClient

_XML = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip><value>1000000</value>
    <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority><Sole>5000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>COCA COLA CO</nameOfIssuer><titleOfClass>COM</titleOfClass>
    <cusip>191216100</cusip><value>2000000</value>
    <shrsOrPrnAmt><sshPrnamt>8000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority><Sole>8000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
</informationTable>"""


class _FakeHttp:
    """Returns index.json for *index.json and the XML otherwise (EdgarClient._get path)."""

    def get(self, url):
        if url.endswith("index.json"):
            body = {"directory": {"item": [
                {"name": "primary_doc.xml", "size": "1200"},
                {"name": "infotable.xml", "size": "9000"}]}}
            return _Resp(json_body=body)
        return _Resp(text_body=_XML)


class _Resp:
    def __init__(self, json_body=None, text_body=None):
        self._j, self.text, self.status_code = json_body, text_body, 200

    def json(self):
        return self._j

    def raise_for_status(self):
        return None


def _filing():
    return Filing(cik=1067983, form="13F-HR", filing_date="2024-08-14",
                  report_date="2024-06-30", accession="0001067983-24-000011",
                  primary_document="primary_doc.html")


def test_parse_info_table_tidy():
    df = ThirteenFClient.parse_info_table(_XML)
    assert list(df["issuer"]) == ["APPLE INC", "COCA COLA CO"]
    assert df["cusip"].tolist() == ["037833100", "191216100"]
    assert df["shares"].tolist() == [5000, 8000]
    assert df["value"].tolist() == [1000000, 2000000]


def test_parse_handles_garbage():
    assert ThirteenFClient.parse_info_table("not xml").empty


def test_fetch_holdings_stamps_and_scales_whole_dollars():
    # implied price = 1_000_000/5000 = $200 → plausible → whole dollars (scale 1)
    client = ThirteenFClient(EdgarClient(http=_FakeHttp()))
    df = client.fetch_holdings(_filing())
    assert df["value_usd"].tolist() == [1000000, 2000000]
    assert (df["period_of_report"] == "2024-06-30").all()
    assert (df["filed"] == "2024-08-14").all()


def test_value_scale_autodetects_thousands():
    # implied price 0.2/0.25 (<$1) ⇒ values are in $thousands ⇒ ×1000
    df = pd.DataFrame({"sh_type": ["SH", "SH"], "shares": [5000, 8000], "value": [1000, 2000]})
    assert ThirteenFClient._value_scale(df) == 1000
    # implied price $200 ⇒ whole dollars
    df2 = pd.DataFrame({"sh_type": ["SH"], "shares": [5000], "value": [1_000_000]})
    assert ThirteenFClient._value_scale(df2) == 1


def test_holdings_store_roundtrip_and_catalog(tmp_path):
    store = HoldingsStore(tmp_path)
    df = ThirteenFClient(EdgarClient(http=_FakeHttp())).fetch_holdings(_filing())
    store.save(1067983, "Berkshire Hathaway", "2024-06-30", df)
    assert store._path(1067983, "2024-06-30").parts[-3:] == ("13f", "1067983", "2024-06-30.parquet")
    out = store.load(1067983, "2024-06-30")
    assert out["manager"].iloc[0] == "Berkshire Hathaway" and len(out) == 2
    cat = store.catalog()
    assert cat.iloc[0]["positions"] == 2 and cat.iloc[0]["cik"] == 1067983


def test_taxonomy_registers_ownership_domain():
    from qhfi.data.taxonomy import DataDomain, by_domain

    assert "institutional_holdings_13f" in {d.name for d in by_domain(DataDomain.OWNERSHIP)}
