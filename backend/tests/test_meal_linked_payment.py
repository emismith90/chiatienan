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
