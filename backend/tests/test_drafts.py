from datetime import date

from app import drafts
from app.models import Meal, RoomMessage
from tests.test_ledger import _seed_room


def _payload(a, b, c, total=400_000, guests=None):
    return {
        "payer_member_id": a, "member_participants": [a, b, c], "guests": guests or [],
        "bill_total": total, "adjustments": [], "dish": None, "initiator": None,
        "note": None, "per_head_preview": total // (3 + len(guests or [])),
        "raw_input": "@bot test",
    }


def test_create_draft_is_pending(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        assert d.kind == "expense_draft"
        assert d.attachments["status"] == "pending"


def test_commit_draft_writes_meal_and_balances(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by=str(a))
        assert meal_msg.kind == "bot"
        att = meal_msg.attachments
        assert att["type"] == "meal"
        assert att["bill_total"] == 400_000
        # 400_000 split 3 ways (remainder to payer, per money.split_shares) ->
        # shares {133_334, 133_333, 133_333}; payer paid 400_000, consumed
        # 133_334 -> owed 266_666.
        assert any(row["balance"] == 266_666 for row in att["balances"])  # payer owed
        assert s.query(Meal).count() == 1
        assert s.get(RoomMessage, d.id).attachments["status"] == "committed"


def test_second_draft_supersedes_first(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d1 = drafts.create_draft(s, room_id, _payload(a, b, c))
        d2 = drafts.create_draft(s, room_id, _payload(b, a, c))
        assert s.get(RoomMessage, d1.id).attachments["status"] == "committed"
        assert d2.attachments["status"] == "pending"
        assert drafts.get_pending_draft(s, room_id).id == d2.id
        assert s.query(Meal).count() == 1   # only d1 committed so far


def test_cancel_writes_nothing(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"status": "cancelled"})
        assert s.get(RoomMessage, d.id).attachments["status"] == "cancelled"
        assert s.query(Meal).count() == 0
        assert drafts.get_pending_draft(s, room_id) is None


def test_edit_then_supersede_saves_edits(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"member_participants": [a, b]})  # drop c
        drafts.create_draft(s, room_id, _payload(b, a, c))                       # supersede
        meal = s.query(Meal).one()
        member_ids = {sh.member_id for sh in meal.shares}
        assert member_ids == {a, b}   # committed the edited set, not the original
