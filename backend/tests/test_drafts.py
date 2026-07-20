from datetime import date, timedelta

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


def test_second_draft_supersedes_first(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d1, extras1 = drafts.create_draft(s, room_id, _payload(a, b, c))
        d2, extras2 = drafts.create_draft(s, room_id, _payload(b, a, c))
        assert s.get(RoomMessage, d1.id).attachments["status"] == "committed"
        assert d2.attachments["status"] == "pending"
        assert drafts.get_pending_draft(s, room_id).id == d2.id
        assert s.query(Meal).count() == 1   # only d1 committed so far
        assert extras1 == []           # no prior pending draft for d1
        assert len(extras2) == 2       # d2 superseded d1: committed draft + meal card


def test_supersede_returns_extras_for_publishing(db):
    """Fix 1: create_draft must hand back the messages a supersede-commit
    produced (the flipped-to-committed prior draft + its meal card), so the
    caller can publish them over SSE — commit_draft's DB writes alone never
    reach live clients."""
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d1, extras1 = drafts.create_draft(s, room_id, _payload(a, b, c))
        assert extras1 == []  # no pending draft to supersede yet

        d2, extras2 = drafts.create_draft(s, room_id, _payload(b, a, c))
        assert len(extras2) == 2
        committed_prev, meal_msg = extras2
        assert committed_prev.id == d1.id
        assert committed_prev.attachments["status"] == "committed"
        assert meal_msg.kind == "bot"
        assert meal_msg.attachments["type"] == "meal"
        assert meal_msg.attachments["meal_id"] == committed_prev.attachments["committed_meal_id"]


def test_supersede_of_invalid_prior_draft_cancels_instead_of_committing(db):
    """Fix 3: if the prior pending draft was edited into an invalid state
    (here: bill_total edited to 0), committing it would raise MoneyError.
    create_draft must not lose the new proposal — it cancels the broken prior
    draft instead, and the new draft still becomes pending."""
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d1, _ = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d1.id, room_id, {"bill_total": 0})

        d2, extras = drafts.create_draft(s, room_id, _payload(b, a, c))

        prev = s.get(RoomMessage, d1.id)
        assert prev.attachments["status"] == "cancelled"
        assert "committed_meal_id" not in prev.attachments
        assert s.query(Meal).count() == 0   # no Meal row for the broken draft
        assert d2.attachments["status"] == "pending"
        assert extras == [prev]


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


def test_supersede_of_prior_draft_with_null_bill_total_cancels_instead_of_committing(db):
    """Same bug via the supersede path: a prior pending draft edited to have
    bill_total=None must not raise a bare TypeError inside create_draft's
    except (MoneyError, LedgerError) — that would lose the new proposal
    entirely. It must be caught as LedgerError and the prior draft cancelled,
    with the new draft still becoming pending."""
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d1, _ = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d1.id, room_id, {"bill_total": None})

        d2, extras = drafts.create_draft(s, room_id, _payload(b, a, c))

        prev = s.get(RoomMessage, d1.id)
        assert prev.attachments["status"] == "cancelled"
        assert "committed_meal_id" not in prev.attachments
        assert s.query(Meal).count() == 0
        assert d2.attachments["status"] == "pending"
        assert extras == [prev]


def test_cancel_writes_nothing(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _extras = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"status": "cancelled"})
        assert s.get(RoomMessage, d.id).attachments["status"] == "cancelled"
        assert s.query(Meal).count() == 0
        assert drafts.get_pending_draft(s, room_id) is None


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


def test_edit_then_supersede_saves_edits(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _extras = drafts.create_draft(s, room_id, _payload(a, b, c))
        drafts.update_draft(s, d.id, room_id, {"member_participants": [a, b]})  # drop c
        drafts.create_draft(s, room_id, _payload(b, a, c))                       # supersede
        meal = s.query(Meal).one()
        member_ids = {sh.member_id for sh in meal.shares}
        assert member_ids == {a, b}   # committed the edited set, not the original
