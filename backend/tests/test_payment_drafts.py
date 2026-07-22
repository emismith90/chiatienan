import pytest

from app import chat, drafts, ledger
from app.models import Payment
from app.tools import ToolContext, build_tools
from tests.test_ledger import _seed_room


@pytest.fixture
def room(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    ids = {"room": room_id, "alice": a, "bob": b, "carol": c}
    return db, ids


def test_create_and_commit_payment_draft(room):
    db, ids = room
    with db.session() as s:
        d = drafts.create_payment_draft(s, ids["room"], {"transfers": [
            {"from_member_id": ids["alice"], "to_member_id": ids["bob"],
             "amount": 50000, "note": None}]})
        draft_id = d.id
        assert d.kind == "payment_draft"
        assert (d.attachments or {})["status"] == "pending"
    with db.session() as s:
        card = drafts.commit_any(s, draft_id, ids["room"], logged_by="test")
        assert card.kind == "bot"
    with db.session() as s:
        assert s.query(Payment).count() == 1


def test_commit_multi_transfer_payment_draft(room):
    db, ids = room
    with db.session() as s:
        d = drafts.create_payment_draft(s, ids["room"], {"transfers": [
            {"from_member_id": ids["alice"], "to_member_id": ids["carol"],
             "amount": 30000, "note": None},
            {"from_member_id": ids["bob"], "to_member_id": ids["carol"],
             "amount": 20000, "note": None}]})
        draft_id = d.id
    with db.session() as s:
        card = drafts.commit_any(s, draft_id, ids["room"], logged_by="test")
        assert card.kind == "bot"
        assert len((card.attachments or {})["transfers"]) == 2
    with db.session() as s:
        assert s.query(Payment).count() == 2


def test_commit_twice_is_rejected(room):
    db, ids = room
    with db.session() as s:
        d = drafts.create_payment_draft(s, ids["room"], {"transfers": [
            {"from_member_id": ids["alice"], "to_member_id": ids["bob"],
             "amount": 1000, "note": None}]})
        draft_id = d.id
    with db.session() as s:
        drafts.commit_any(s, draft_id, ids["room"], logged_by="t")
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            drafts.commit_any(s, draft_id, ids["room"], logged_by="t")


def test_pending_list_includes_payment_drafts(room):
    db, ids = room
    with db.session() as s:
        drafts.create_payment_draft(s, ids["room"], {"transfers": [
            {"from_member_id": ids["alice"], "to_member_id": ids["bob"],
             "amount": 1000, "note": None}]})
    with db.session() as s:
        pending = drafts.list_pending_drafts(s, ids["room"])
        assert len(pending) == 1 and pending[0].kind == "payment_draft"


def test_settle_blocked_by_pending_payment_draft(room):
    db, ids = room
    with db.session() as s:
        drafts.create_payment_draft(s, ids["room"], {"transfers": [
            {"from_member_id": ids["alice"], "to_member_id": ids["bob"],
             "amount": 50000, "note": None}]})
    ctx = ToolContext(db=db, room_id=ids["room"], sender_member_id=ids["alice"],
                      sender_name="M1", turn_mentions=[])
    out = build_tools(ctx)["settle_period"].execute({"keyword": "since_last"})
    assert out["type"] == "settle_blocked"
    pending = out["pending"]
    assert len(pending) == 1
    p = pending[0]
    assert p["kind"] == "payment"
    assert p["transfers"][0]["amount"] == 50000
    # Deterministic body renders the payer→payee + amount line.
    body = chat._settle_blocked_body(out)
    assert "M1" in body and "M2" in body and "50,000đ" in body
