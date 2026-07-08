"""Account-CRUD router guards: create/list + the delete protections (default account, last account,
algos referencing the book). Exercises the `/api/paper/accounts` handlers directly with the heavy
deps (get_store / get_sim_broker / get_broker) monkeypatched onto a tmp store.

Run: `cd backend && pytest tests/test_paper_accounts_router.py -v`
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.routers import paper as paper_router
from app.services.broker import SimBroker
from app.store import TerminalStore


@pytest.fixture()
def patched(tmp_path, monkeypatch):
    store = TerminalStore(tmp_path / "term.db")
    store.init()

    # get_sim_broker(account_id) → a real SimBroker bound to that account (dm=None: no trading here),
    # carrying a .cache_clear() the handlers call after mutations.
    def fake_sim(account_id: int = 1):
        return SimBroker(store, dm=None, initial_cash=100_000.0, account_id=account_id)

    fake_sim.cache_clear = MagicMock()  # type: ignore[attr-defined]
    fake_broker = MagicMock()

    monkeypatch.setattr(paper_router, "get_store", lambda: store)
    monkeypatch.setattr(paper_router, "get_sim_broker", fake_sim)
    monkeypatch.setattr(paper_router, "get_broker", fake_broker)
    return store


def test_create_and_list(patched):
    paper_router.list_accounts()  # ensures the Default account exists
    out = paper_router.create_account(paper_router.AccountCreateIn(name="Momentum", initial_cash=250_000))
    assert out["ok"] and out["account"]["name"] == "Momentum"
    accounts = paper_router.list_accounts()["accounts"]
    names = {a["name"] for a in accounts}
    assert {"Default", "Momentum"} <= names


def test_create_rejects_blank_name(patched):
    with pytest.raises(HTTPException) as e:
        paper_router.create_account(paper_router.AccountCreateIn(name="  ", initial_cash=100_000))
    assert e.value.status_code == 400


def test_delete_default_blocked(patched):
    paper_router.list_accounts()
    with pytest.raises(HTTPException) as e:
        paper_router.delete_account(1)
    assert e.value.status_code == 400 and "Default" in e.value.detail


def test_delete_last_account_blocked(patched):
    # Archive the Default account so the only live account is the new one → can't delete it either.
    paper_router.list_accounts()
    aid = paper_router.create_account(paper_router.AccountCreateIn(name="Solo", initial_cash=100_000))["account"]["id"]
    patched.archive_paper_account(1)
    with pytest.raises(HTTPException) as e:
        paper_router.delete_account(aid)
    assert e.value.status_code == 400 and "last account" in e.value.detail


def test_delete_blocked_while_armed_algo_references_it(patched):
    paper_router.list_accounts()
    aid = paper_router.create_account(paper_router.AccountCreateIn(name="AlgoBook", initial_cash=100_000))["account"]["id"]
    # an armed algo trading sim:<aid> must block the delete
    patched._conn.execute(
        "INSERT INTO algos(id, data, updated) VALUES (?, ?, datetime('now'))",
        ("a1", json.dumps({"name": "MA cross", "book": f"sim:{aid}", "armed": True})),
    )
    patched._conn.commit()
    with pytest.raises(HTTPException) as e:
        paper_router.delete_account(aid)
    assert e.value.status_code == 400 and "MA cross" in e.value.detail


def test_delete_succeeds_when_unreferenced(patched):
    paper_router.list_accounts()
    aid = paper_router.create_account(paper_router.AccountCreateIn(name="Scratch", initial_cash=100_000))["account"]["id"]
    assert paper_router.delete_account(aid)["ok"]
    assert patched.get_paper_account(aid) is None


def test_http_accounts_crud_and_account_query_binding(patched):
    """Through the ASGI stack: the accounts CRUD endpoints + ?account= query binding round-trip."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(paper_router.router)
    client = TestClient(app)

    assert "Default" in {a["name"] for a in client.get("/api/paper/accounts").json()["accounts"]}

    r = client.post("/api/paper/accounts", json={"name": "BookB", "initial_cash": 500_000})
    assert r.status_code == 200, r.text
    acct = r.json()["account"]
    assert acct["name"] == "BookB" and acct["cash"] == pytest.approx(500_000.0)
    bid = acct["id"]

    # ?account= binds (int query param) and per-account realism PATCH round-trips
    patch = client.patch(f"/api/paper/accounts/{bid}", json={"commission_bps": 12.5})
    assert patch.status_code == 200 and patch.json()["account"]["commission_bps"] == 12.5
    cfg = client.get(f"/api/paper/config?account={bid}").json()
    assert cfg["commission_bps"] == 12.5 and "broker" in cfg

    # delete guard surfaces as HTTP 400 (default account is undeletable)
    assert client.delete("/api/paper/accounts/1").status_code == 400
