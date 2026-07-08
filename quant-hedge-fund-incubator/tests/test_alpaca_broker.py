"""Offline test for the Alpaca broker mapping — a fake TradingClient (no alpaca-py, no
network) verifies that submit/get_account/get_positions translate correctly."""

from __future__ import annotations

from types import SimpleNamespace

from qhfi.execution.base import Order, OrderSide
from qhfi.execution.brokers import alpaca_paper
from qhfi.execution.brokers.alpaca_paper import AlpacaPaperBroker


class _FakeClient:
    def __init__(self):
        self.last_req = None
        self.cancelled: list[str] = []
        self.closed: list[str] = []
        self.flattened = False

    def get_account(self):
        return SimpleNamespace(equity="100000", cash="50000")

    def get_all_positions(self):
        return [SimpleNamespace(symbol="AAPL", qty="10", avg_entry_price="150.5")]

    def submit_order(self, req):
        self.last_req = req
        return SimpleNamespace(id="order-abc-123")

    def close_position(self, symbol):
        self.closed.append(symbol)
        return SimpleNamespace(id=f"close-{symbol}")

    def close_all_positions(self, cancel_orders=False):
        self.flattened = cancel_orders
        return [SimpleNamespace(body=SimpleNamespace(id="close-AAPL"))]

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)

    def get_orders(self, filter=None):  # noqa: A002 - mirror alpaca-py kwarg name
        return [
            SimpleNamespace(
                id="ord-1", submitted_at=None, symbol="AAPL",
                side=SimpleNamespace(value="buy"), qty="10",
                type=SimpleNamespace(value="trailing_stop"), limit_price=None,
                status=SimpleNamespace(value="new"), filled_avg_price=None,
                stop_price="148.0", trail_percent="3.0",
            ),
        ]


def test_get_account_and_positions_map_correctly():
    broker = AlpacaPaperBroker(client=_FakeClient())
    acct = broker.get_account()
    assert acct.equity == 100000.0 and acct.cash == 50000.0
    assert acct.positions["AAPL"].quantity == 10.0
    assert acct.positions["AAPL"].avg_price == 150.5


def test_submit_returns_order_id_and_builds_request(monkeypatch):
    # avoid importing alpaca-py: stub the request builder, capture the order it received
    captured = {}

    def fake_build(order, stop_price=None, trail_pct=None):
        captured["order"] = order
        captured["stop_price"] = stop_price
        captured["trail_pct"] = trail_pct
        return {"req": order.instrument_id}

    monkeypatch.setattr(alpaca_paper, "_build_order_request", fake_build)

    client = _FakeClient()
    broker = AlpacaPaperBroker(client=client)
    oid = broker.submit(Order(instrument_id="MSFT", side=OrderSide.BUY, quantity=5),
                        stop_price=100.0, trail_pct=2.5)

    assert oid == "order-abc-123"
    assert captured["order"].instrument_id == "MSFT" and captured["order"].side is OrderSide.BUY
    assert captured["stop_price"] == 100.0 and captured["trail_pct"] == 2.5
    assert client.last_req == {"req": "MSFT"}


def test_build_request_handles_advanced_types():
    from qhfi.execution.brokers.alpaca_paper import _build_order_request

    stop = _build_order_request(Order(instrument_id="AAPL", side=OrderSide.SELL, quantity=3,
                                      type="stop"), stop_price=140.0)
    assert stop.stop_price == 140.0 and stop.qty == 3

    trail = _build_order_request(Order(instrument_id="AAPL", side=OrderSide.SELL, quantity=3,
                                       type="trailing_stop"), trail_pct=3.0)
    assert trail.trail_percent == 3.0

    import pytest

    with pytest.raises(ValueError):
        _build_order_request(Order(instrument_id="AAPL", side=OrderSide.SELL, quantity=3,
                                   type="stop"))  # missing stop_price


def test_advanced_ops_map_to_client():
    client = _FakeClient()
    broker = AlpacaPaperBroker(client=client)

    assert broker.close_position("aapl") == "close-AAPL" and client.closed == ["AAPL"]
    assert broker.flatten_all() == ["close-AAPL"] and client.flattened is True
    assert broker.cancel("ord-1") is True and client.cancelled == ["ord-1"]

    orders = broker.list_orders()
    assert orders[0]["id"] == "ord-1" and orders[0]["type"] == "trailing_stop"
    assert orders[0]["status"] == "open" and orders[0]["trail_pct"] == 3.0
    assert orders[0]["broker_order_id"] == "ord-1"
