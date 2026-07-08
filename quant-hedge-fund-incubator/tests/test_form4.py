"""Offline tests for insider filings: Form 4 parser, InsiderClient (mocked EDGAR),
InsiderStore roundtrip, and HoldingsStore.holders_of (13F holders-of-ticker lookup)."""

from __future__ import annotations

import pandas as pd

from qhfi.data.crosswalk import CusipTickerStore
from qhfi.data.holdings import HoldingsStore
from qhfi.data.insider import InsiderStore
from qhfi.data.providers.edgar import EdgarClient, Filing
from qhfi.data.providers.form4 import InsiderClient, parse_form4

_FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerName>APPLE INC</issuerName><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>COOK TIMOTHY D</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2024-04-01</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>170.0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>50000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2024-03-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>165.0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeHolding>
      <securityTitle><value>Common Stock</value></securityTitle>
    </nonDerivativeHolding>
  </nonDerivativeTable>
</ownershipDocument>"""


class _Resp:
    def __init__(self, json_body=None, text_body=None):
        self._j, self.text, self.status_code = json_body, text_body, 200

    def json(self):
        return self._j

    def raise_for_status(self):
        return None


class _FakeHttp:
    """index.json lists a small primary doc + a larger form4 xml; everything else is the XML."""

    def get(self, url):
        if url.endswith("index.json"):
            return _Resp(json_body={"directory": {"item": [
                {"name": "primary_doc.xml", "size": "400"},
                {"name": "form4.xml", "size": "4000"}]}})
        return _Resp(text_body=_FORM4)


def _filing():
    return Filing(cik=320193, form="4", filing_date="2024-04-02", report_date="",
                  accession="0000320193-24-000050", primary_document="form4.xml")


def test_parse_form4_transactions():
    df = parse_form4(_FORM4)
    assert len(df) == 2  # two transactions, the no-trade holding is dropped
    assert df["insider"].tolist() == ["COOK TIMOTHY D", "COOK TIMOTHY D"]
    assert df["role"].iloc[0] == "Officer (CEO)"
    assert df["code"].tolist() == ["S", "P"]
    assert df["acq_disp"].tolist() == ["D", "A"]
    assert df["shares"].tolist() == [1000.0, 500.0]
    assert df["price"].tolist() == [170.0, 165.0]
    assert df["shares_after"].iloc[0] == 50000.0
    assert not df["derivative"].any()


def test_parse_form4_handles_garbage():
    assert parse_form4("not xml").empty


def test_insider_client_fetch_stamps_metadata():
    client = InsiderClient(EdgarClient(http=_FakeHttp()))
    df = client.fetch_transactions(_filing())
    assert len(df) == 2
    assert (df["form"] == "4").all()
    assert (df["filed"] == "2024-04-02").all()
    assert (df["accession"] == "0000320193-24-000050").all()


def test_insider_store_roundtrip_and_dedup(tmp_path):
    store = InsiderStore(tmp_path)
    df = InsiderClient(EdgarClient(http=_FakeHttp())).fetch_transactions(_filing())
    assert store.save("AAPL", df) == 2
    assert store.has("AAPL") and store.has_accession("AAPL", "0000320193-24-000050")
    assert store.save("AAPL", df) == 2  # idempotent re-pull
    out = store.load("AAPL")
    assert out["ticker"].iloc[0] == "AAPL" and len(out) == 2
    cat = store.catalog()
    assert cat.iloc[0]["transactions"] == 2


def _holdings(period: str, rows: list[dict]) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    out["period_of_report"] = period
    out["filed"] = period
    return out


def test_holders_of_latest_period_with_qoq(tmp_path):
    hs = HoldingsStore(tmp_path)
    cx = CusipTickerStore(tmp_path)
    cx.upsert({"037833100": {"ticker": "AAPL", "name": "APPLE INC", "exch": "Q", "sec_type": "EQ"}})

    # Big Fund: holds AAPL in both quarters (1000 → 1500 shares); 80% of its book is elsewhere.
    hs.save(111, "Big Fund", "2024-03-31",
            _holdings("2024-03-31", [{"cusip": "037833100", "shares": 1000, "value_usd": 100}]))
    hs.save(111, "Big Fund", "2024-06-30",
            _holdings("2024-06-30", [{"cusip": "037833100", "shares": 1500, "value_usd": 200},
                                     {"cusip": "594918104", "shares": 10, "value_usd": 800}]))
    # Small Fund: new AAPL position in the latest quarter only.
    hs.save(222, "Small Fund", "2024-06-30",
            _holdings("2024-06-30", [{"cusip": "037833100", "shares": 500, "value_usd": 50}]))

    res = hs.holders_of("AAPL", cx)
    assert len(res) == 2
    top = res.iloc[0]
    assert top["manager"] == "Big Fund"
    assert top["shares"] == 1500 and top["value_usd"] == 200
    assert top["pct_of_book"] == 20.0          # 200 / (200 + 800)
    assert top["change_shares"] == 500          # 1500 − 1000 prior quarter
    assert res.iloc[1]["manager"] == "Small Fund"


def test_holders_of_unmapped_ticker_is_empty(tmp_path):
    hs = HoldingsStore(tmp_path)
    cx = CusipTickerStore(tmp_path)
    assert hs.holders_of("NOPE", cx).empty


def test_taxonomy_registers_insider_dataset():
    from qhfi.data.taxonomy import DataDomain, by_domain

    assert "insider_transactions_form4" in {d.name for d in by_domain(DataDomain.OWNERSHIP)}
