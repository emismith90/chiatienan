from datetime import date, timedelta

import pytest

from app import drafts, ledger
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
        d, extras = drafts.create_draft(s, room_id, _payload(a, b, c))
        assert d.kind == "expense_draft"
        assert d.attachments["status"] == "pending"
        assert extras == []


def test_commit_draft_writes_meal_and_balances(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _extras = drafts.create_draft(s, room_id, _payload(a, b, c))
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


def test_commit_draft_with_null_bill_total_raises_ledger_error(db):
    """A client can PATCH bill_total to null (DraftPatchIn types it as
    int | None). commit_draft must reject this with a clean LedgerError,
    not blow up with a TypeError from int(None)."""
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"bill_total": None})

        import pytest
        with pytest.raises(ledger.LedgerError):
            drafts.commit_draft(s, d.id, room_id, logged_by=str(a))
        assert s.query(Meal).count() == 0


def test_cancel_writes_nothing(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _extras = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"status": "cancelled"})
        assert s.get(RoomMessage, d.id).attachments["status"] == "cancelled"
        assert s.query(Meal).count() == 0
        assert drafts.list_pending_drafts(s, room_id) == []


def test_current_balances_excludes_settlement_boundary_day(db):
    """A meal recorded exactly on the last settlement's period_to must NOT be
    re-counted in current_balances (regression: period_balances is inclusive
    on from_date, so naively passing period_to as from double-counts)."""
    room_id, (a, b, c) = _seed_room(db, 3)
    D = date(2020, 1, 10)  # far in the past, guaranteed to be before "today"
    with db.session() as s:
        # Pre-settlement meal that lands exactly on the boundary day.
        ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b],
            total_amount=300_000, occurred_on=D,
        )
        ledger.record_settlement(
            s, room_id=room_id, period_from=None, period_to=D,
            requested_by=None, transfers=[],
        )
        # Post-settlement meal, a few days after the boundary.
        ledger.record_meal(
            s, room_id=room_id, payer_member_id=b, participants=[b, c],
            total_amount=200_000, occurred_on=D + timedelta(days=5),
        )
        d, _extras = drafts.create_draft(s, room_id, _payload(c, a, b, total=300_000))
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by=str(c))

    balances = {row["id"]: row for row in meal_msg.attachments["balances"]}
    # `a` only ever consumed in the drafted meal (100_000) post-settlement;
    # if the boundary-day meal were double-counted, `a` would also carry
    # the 300_000 paid / 150_000 consumed from the settled meal.
    assert balances[a]["paid"] == 0
    assert balances[a]["consumed"] == 100_000
    assert balances[a]["balance"] == -100_000


def test_edit_then_commit_saves_edits(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _extras = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"member_participants": [a, b]})  # drop c
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by=str(a))
        meal = s.get(Meal, meal_msg.attachments["meal_id"])
        member_ids = {sh.member_id for sh in meal.shares}
        assert member_ids == {a, b}   # committed the edited set, not the original


def test_recommit_draft_edits_committed_meal(db):
    from app.models import Meal, RoomMessage
    from app import drafts, ledger
    from tests.test_ledger import _seed_room
    room_id, ids = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": ids[0], "member_participants": ids, "guests": [],
            "bill_total": 300, "adjustments": [], "per_head_preview": 100, "raw_input": "x"})
        drafts.commit_draft(s, d.id, room_id, logged_by="1")
        old_meal_id = s.get(RoomMessage, d.id).attachments["committed_meal_id"]
        drafts.recommit_draft(s, d.id, room_id, {"bill_total": 600}, logged_by="1")
        att = s.get(RoomMessage, d.id).attachments
        assert att["committed_meal_id"] != old_meal_id
        assert s.get(Meal, old_meal_id).voided is True
        assert s.get(Meal, att["committed_meal_id"]).total_amount == 600


def test_recommit_blocked_when_meal_is_settled(db):
    from datetime import date
    from app.models import RoomMessage
    from app import drafts, ledger
    from tests.test_ledger import _seed_room
    room_id, ids = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": ids[0], "member_participants": ids, "guests": [],
            "bill_total": 300, "adjustments": [], "per_head_preview": 100, "raw_input": "x"})
        meal_msg = drafts.commit_draft(s, d.id, room_id, logged_by="1")
        occurred = date.fromisoformat(meal_msg.attachments["occurred_on"])
        ledger.record_settlement(s, room_id=room_id, period_from=None,
                                 period_to=occurred, requested_by="1", transfers=[])
        with pytest.raises(ledger.LedgerError):
            drafts.recommit_draft(s, d.id, room_id, {"bill_total": 600}, logged_by="1")


# --- superseding a re-proposal (production #101 deadlock) ------------------- #

def test_reproposing_the_same_meal_retires_the_older_card(db):
    """Production: a 324,000đ proposal was refined into 324,200đ twenty messages
    later. Confirming the new card left the old one pending, so every settle for
    the next four hours answered "#101 chưa xác nhận"."""
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        first, _ = drafts.create_draft(s, room_id, _payload(a, b, c, total=324_000))
        second, superseded = drafts.create_draft(s, room_id, _payload(a, b, c, total=324_200))

        assert [m.id for m in superseded] == [first.id]
        assert s.get(RoomMessage, first.id).attachments["status"] == "superseded"
        assert second.attachments["status"] == "pending"
        assert [m.id for m in drafts.list_pending_drafts(s, room_id)] == [second.id]


def test_superseding_records_nothing(db):
    """An earlier version auto-committed the loser (641ffa7). Retiring a card
    the humans never confirmed must not touch the ledger."""
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        drafts.create_draft(s, room_id, _payload(a, b, c, total=324_000))
        drafts.create_draft(s, room_id, _payload(a, b, c, total=324_200))
        assert s.query(Meal).count() == 0


def test_a_different_meal_is_left_alone(db):
    """Different participants means a real second meal, not a re-proposal."""
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        first, _ = drafts.create_draft(s, room_id, _payload(a, b, c))
        other = dict(_payload(a, b, c), member_participants=[a, b])
        _second, superseded = drafts.create_draft(s, room_id, other)
        assert superseded == []
        assert len(drafts.list_pending_drafts(s, room_id)) == 2
        assert s.get(RoomMessage, first.id).attachments["status"] == "pending"


def test_a_committed_card_is_never_superseded(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        first, _ = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.commit_draft(s, first.id, room_id, logged_by=str(a))
        _second, superseded = drafts.create_draft(s, room_id, _payload(a, b, c))
        assert superseded == []
        assert s.get(RoomMessage, first.id).attachments["status"] == "committed"


def test_repeated_payment_draft_retires_the_older_one(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        transfers = [{"from_member_id": a, "to_member_id": b, "amount": 27_000, "note": None}]
        first, _ = drafts.create_payment_draft(s, room_id, {"transfers": transfers})
        _second, superseded = drafts.create_payment_draft(s, room_id, {"transfers": transfers})
        assert [m.id for m in superseded] == [first.id]
        assert s.get(RoomMessage, first.id).attachments["status"] == "superseded"


def test_a_superseded_card_no_longer_blocks_a_settle(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        drafts.create_draft(s, room_id, _payload(a, b, c, total=324_000))
        second, _ = drafts.create_draft(s, room_id, _payload(a, b, c, total=324_200))
        drafts.commit_draft(s, second.id, room_id, logged_by=str(a))
        assert drafts.list_pending_drafts(s, room_id) == []
