import pytest

from app.tools import ToolContext, build_tools
from app import ledger
from tests.test_ledger import _seed_room


@pytest.fixture
def room(db):
    room_id, (a, b) = _seed_room(db, 2)
    ids = {"room": room_id, "alice": a, "bob": b}
    return db, ids


def _tools(db, ids):
    ctx = ToolContext(db=db, room_id=ids["room"], sender_member_id=ids["alice"],
                      sender_name="Alice", turn_mentions=[])
    return build_tools(ctx)


def test_propose_payment_explicit_amount(room):
    db, ids = room
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["bob"], "amount": 50000})
    assert out["ok"] and out["type"] == "payment_draft"
    assert (out["from_member_id"], out["to_member_id"], out["amount"]) == (ids["alice"], ids["bob"], 50000)


def test_propose_payment_payoff_uses_settle_transfer(room):
    db, ids = room
    # Alice owes Bob: Bob paid a 100k meal for both → Alice owes Bob 50k.
    with db.session() as s:
        ledger.record_meal(s, room_id=ids["room"], payer_member_id=ids["bob"],
                           participants=[ids["alice"], ids["bob"]], total_amount=100000,
                           adjustments={}, guests=[], logged_by="test")
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["bob"]})  # no amount → pay off
    assert out["type"] == "payment_draft" and out["amount"] == 50000


def test_propose_payment_nothing_owed_returns_settled(room):
    db, ids = room
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["bob"]})  # no meals → owes nothing
    assert out["ok"] and out["type"] == "payment_settled"


def test_propose_payment_rejects_same_member(room):
    db, ids = room
    t = _tools(db, ids)["propose_payment"]
    out = t.execute({"from": ids["alice"], "to": ids["alice"], "amount": 1000})
    assert out["ok"] is False


def test_propose_payment_rejects_unknown_member(room):
    db, ids = room
    t = _tools(db, ids)["propose_payment"]
    # `to` id 999999 is not a member of the room → error, and must NOT fall
    # through the pay-off path and report payment_settled.
    out = t.execute({"from": ids["alice"], "to": 999999})
    assert out["ok"] is False
    assert out.get("type") != "payment_settled"
