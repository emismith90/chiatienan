import pytest

from app import drafts, ledger
from app.models import Payment
from tests.test_ledger import _seed_room


@pytest.fixture
def room(db):
    room_id, (a, b) = _seed_room(db, 2)
    ids = {"room": room_id, "alice": a, "bob": b}
    return db, ids


def test_create_and_commit_payment_draft(room):
    db, ids = room
    with db.session() as s:
        d = drafts.create_payment_draft(s, ids["room"], {
            "from_member_id": ids["alice"], "to_member_id": ids["bob"],
            "amount": 50000, "note": None})
        draft_id = d.id
        assert d.kind == "payment_draft"
        assert (d.attachments or {})["status"] == "pending"
    with db.session() as s:
        card = drafts.commit_any(s, draft_id, ids["room"], logged_by="test")
        assert card.kind == "bot"
    with db.session() as s:
        assert s.query(Payment).count() == 1


def test_commit_twice_is_rejected(room):
    db, ids = room
    with db.session() as s:
        d = drafts.create_payment_draft(s, ids["room"], {
            "from_member_id": ids["alice"], "to_member_id": ids["bob"], "amount": 1000, "note": None})
        draft_id = d.id
    with db.session() as s:
        drafts.commit_any(s, draft_id, ids["room"], logged_by="t")
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            drafts.commit_any(s, draft_id, ids["room"], logged_by="t")


def test_pending_list_includes_payment_drafts(room):
    db, ids = room
    with db.session() as s:
        drafts.create_payment_draft(s, ids["room"], {
            "from_member_id": ids["alice"], "to_member_id": ids["bob"], "amount": 1000, "note": None})
    with db.session() as s:
        pending = drafts.list_pending_drafts(s, ids["room"])
        assert len(pending) == 1 and pending[0].kind == "payment_draft"
