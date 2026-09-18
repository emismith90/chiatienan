from datetime import date
import pytest
from sqlalchemy import select
from app.db import Database
from app import ledger
from app.models import Payment, RoomMessage


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/t.db"); d.create_all(); return d


def test_meal_linked_payment_marks_that_meal(db):
    from app.models import Room, Member
    with db.session() as s:
        r = Room(name="t", invite_token="tok"); s.add(r); s.flush()
        linh = Member(room_id=r.id, display_name="Linh", nickname="Linh"); s.add(linh); s.flush()
        giang = Member(room_id=r.id, display_name="Giang", nickname="Giang"); s.add(giang); s.flush()
        m2 = ledger.record_meal(s, room_id=r.id, payer_member_id=linh.id,
                                participants=[linh.id, giang.id], total_amount=122000,
                                dish="older", occurred_on=date(2026, 7, 21))["meal_id"]
        m5 = ledger.record_meal(s, room_id=r.id, payer_member_id=linh.id,
                                participants=[linh.id, giang.id], total_amount=80000,
                                dish="newer", occurred_on=date(2026, 7, 24))["meal_id"]
        # Giang pays off the NEWER meal specifically
        ledger.record_payment(s, room_id=r.id, from_member_id=giang.id, to_member_id=linh.id,
                              amount=40000, occurred_on=date(2026, 7, 24), meal_id=m5)
        edges = {e.meal_id: e for e in ledger.debt_breakdown(s, r.id, None, date(2026, 7, 24))
                 if e.debtor == giang.id}
        assert edges[m5].status == "paid" and edges[m2].status == "unpaid"


def test_editing_a_committed_meal_keeps_its_quick_paid_money(db):
    """Tabu taps ⑦ to clear her exact share, then the payer edits the total.
    Her payment used to be stranded on the voided meal: her statement said
    "unpaid" while the settlement counted it — a 50,000đ disagreement."""
    from app import drafts, ledger
    from tests.test_ledger import _seed_room

    room_id, (payer, tabu) = _seed_room(db, 2)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": payer, "member_participants": [payer, tabu], "guests": [],
            "bill_total": 100_000, "adjustments": [], "dish": "pho", "initiator": None,
            "note": None, "per_head_preview": 50_000, "raw_input": "@phoenix pho 100k",
        })
        drafts.commit_draft(s, d.id, room_id, logged_by=str(payer))
        meal_id = s.get(RoomMessage, d.id).attachments["committed_meal_id"]
        ledger.record_payment(s, room_id=room_id, from_member_id=tabu, to_member_id=payer,
                              amount=50_000, meal_id=meal_id, logged_by=str(tabu))

        # The payer corrects the bill: 100k was really 110k.
        drafts.recommit_draft(s, d.id, room_id, {"bill_total": 110_000}, logged_by=str(payer))
        new_meal_id = s.get(RoomMessage, d.id).attachments["committed_meal_id"]

        # The totals agreeing is necessary but not sufficient — it's also what the
        # dead repoint used to produce, by accident, once the payment fell into the
        # (tabu, payer) pair pool. What actually matters is that the payment is
        # TARGETED at the edited meal's new id, not floating loose in the pool.
        pay = s.scalars(select(Payment).where(
            Payment.room_id == room_id, Payment.from_member_id == tabu, Payment.to_member_id == payer,
        )).one()
        assert pay.meal_id == new_meal_id

        statement = ledger.statement_for(s, room_id, tabu, None, ledger.today_ict())
        transfers = ledger.period_transfers(s, room_id, None, ledger.today_ict())

    owed_by_statement = sum(r["amount"] for r in statement["owe"])
    owed_by_settle = sum(t.amount for t in transfers if t.from_member == tabu)
    assert owed_by_statement == owed_by_settle == 5_000, (statement, transfers)


def test_editing_a_meal_to_change_the_payer_voids_the_orphaned_quick_pay(db):
    """Regression for the production case: B quick-pays their exact share of a
    meal A paid for; the meal is then edited so the payer becomes C. B's
    payment names A, who is no longer owed anything on this meal — the dead
    repoint used to leave it untargeted, where it fell into the (B, A) pair
    pool and silently paid off a LATER, unrelated meal instead, so B's real
    share of that later meal just vanished from their statement with no trace.

    The fix must instead void the orphaned payment (visibly, with a bot
    message) rather than let it float and cancel a debt nobody paid."""
    from app import drafts, ledger
    from tests.test_ledger import _seed_room

    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        d, _ = drafts.create_draft(s, room_id, {
            "payer_member_id": a, "member_participants": [a, b, c], "guests": [],
            "bill_total": 90_000, "adjustments": [], "dish": "pho", "initiator": None,
            "note": None, "per_head_preview": 30_000, "raw_input": "@phoenix pho 90k",
        })
        drafts.commit_draft(s, d.id, room_id, logged_by=str(a))
        meal_id = s.get(RoomMessage, d.id).attachments["committed_meal_id"]
        # B quick-pays their exact share, targeted at this meal.
        ledger.record_payment(s, room_id=room_id, from_member_id=b, to_member_id=a,
                              amount=30_000, meal_id=meal_id, logged_by=str(b))

        # Correction: the payer was actually C, not A.
        drafts.recommit_draft(s, d.id, room_id, {"payer_member_id": c}, logged_by=str(a))

        # (a) B's payment to A is voided rather than floating in the pair pool.
        pay = s.scalars(select(Payment).where(
            Payment.room_id == room_id, Payment.from_member_id == b, Payment.to_member_id == a,
        )).one()
        assert pay.voided is True

        # (b) the room was told about it.
        bot_messages = s.scalars(select(RoomMessage).where(
            RoomMessage.room_id == room_id, RoomMessage.kind == "bot",
        )).all()
        assert any("30,000" in (msg.body or "") for msg in bot_messages), bot_messages

        # (c) a LATER, unrelated meal A pays for, that B also eats at, still shows
        # up as outstanding for B — this is the exact "my row vanished from the
        # Ledger" symptom: without the fix, B's orphaned payment would silently
        # pay this one off instead of the meal it actually named.
        later = ledger.record_meal(s, room_id=room_id, payer_member_id=a,
                                   participants=[a, b], total_amount=110_000,
                                   dish="later", occurred_on=ledger.today_ict())
        statement = ledger.statement_for(s, room_id, b, None, ledger.today_ict())

    owed_for_later = [row["amount"] for row in statement["owe"] if row["meal_id"] == later["meal_id"]]
    assert owed_for_later == [55_000], statement
