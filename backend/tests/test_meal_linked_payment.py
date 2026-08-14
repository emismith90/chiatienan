from datetime import date
import pytest
from app.db import Database
from app import ledger


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
    from app.models import RoomMessage
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

        statement = ledger.statement_for(s, room_id, tabu, None, ledger.today_ict())
        transfers = ledger.period_transfers(s, room_id, None, ledger.today_ict())

    owed_by_statement = sum(r["amount"] for r in statement["owe"])
    owed_by_settle = sum(t.amount for t in transfers if t.from_member == tabu)
    assert owed_by_statement == owed_by_settle == 5_000, (statement, transfers)
