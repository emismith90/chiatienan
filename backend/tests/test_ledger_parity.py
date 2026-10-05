"""Random ledgers through the real write paths: the journal must read back exactly
what the tables hold, and the money computed from it must be what the tables give
(plan 2026-10-02, release B, B5c)."""
import random
from datetime import date, timedelta

import pytest

from app import ledger
from ledger_core import importer
from ledger_core.models import Meal
from ledger_core.money import apply_payments_fifo, build_debt_edges, net_transfers
from tests.test_ledger import _seed_room

DAY0 = date(2026, 8, 1)


def _table_breakdown(s, room_id, from_date, to_date):
    """``debt_breakdown`` as the pre-journal code computed it, from the tables."""
    meals, payments, _ = importer.table_records(s, room_id)
    edges = build_debt_edges([
        {"meal_id": m.id, "payer_id": m.payer_member_id, "dish": m.dish, "occurred_on": m.occurred_on,
         "shares": {sh.member_id: sh.share_amount for sh in m.shares}} for m in meals if not m.voided])
    pays = [{"from": p.from_member_id, "to": p.to_member_id, "amount": p.amount, "meal_id": p.meal_id,
             "ref_kind": p.ref_kind} for p in payments if not p.voided]
    return [e for e in apply_payments_fifo(edges, pays)
            if e.occurred_on <= to_date and (from_date is None or e.occurred_on >= from_date)]


def _drive(db, room, members, rng, steps):
    meals, payments = [], []
    for _ in range(steps):
        op = rng.choice(["meal", "meal", "meal", "pay", "pay", "void_meal", "void_pay", "retarget", "place", "settle"])
        with db.session() as s:
            if op == "meal" or not meals:
                who = rng.sample(members, rng.randint(1, len(members)))
                res = ledger.record_meal(s, room_id=room, payer_member_id=rng.choice(members), participants=who,
                                         total_amount=rng.randrange(10_000, 600_000, 1_000),
                                         occurred_on=DAY0 + timedelta(days=rng.randint(0, 40)),
                                         dish=rng.choice(["phở", "bún chả", None]))
                meals.append(res["meal_id"])
            elif op == "pay":
                a, b = rng.sample(members, 2)
                res = ledger.record_payment(s, room_id=room, from_member_id=a, to_member_id=b,
                                            amount=rng.randrange(5_000, 300_000, 1_000),
                                            occurred_on=DAY0 + timedelta(days=rng.randint(0, 45)),
                                            meal_id=rng.choice([None, rng.choice(meals)]))
                payments.append(res["payment_id"])
            elif op == "void_meal":
                mid = rng.choice(meals)
                if not s.get(Meal, mid).voided:
                    ledger.void_meal(s, mid, room_id=room, by=str(rng.choice(members)),
                                     untarget_payments=rng.random() < 0.7)
            elif op == "void_pay" and payments:
                ledger.void_payment(s, rng.choice(payments), room_id=room, by="t")
            elif op == "retarget":
                ledger.repoint_meal_payments(s, room_id=room, old_meal_id=rng.choice(meals),
                                             new_meal_id=rng.choice([None, rng.choice(meals)]))
            elif op == "place":
                s.get(Meal, rng.choice(meals)).place_id = rng.choice([None, 1, 2, 3])
            elif op == "settle":
                ledger.record_settlement(s, room_id=room, period_from=None,
                                         period_to=DAY0 + timedelta(days=rng.randint(0, 40)),
                                         transfers=[], requested_by="t")


@pytest.mark.parametrize("seed", range(12))
def test_a_random_ledger_reads_back_identically_from_its_journal(db, seed):
    rng = random.Random(seed)
    room, members = _seed_room(db, rng.randint(2, 5), token=f"t{seed}")
    _drive(db, room, members, rng, steps=60)
    with db.session() as s:
        assert importer.differences(s, room) == []
        for from_date, to_date in [(None, date(2099, 1, 1)), (DAY0 + timedelta(days=10), DAY0 + timedelta(days=25)),
                                   (None, DAY0 + timedelta(days=5)), (DAY0 + timedelta(days=30), DAY0 + timedelta(days=30))]:
            got = ledger.debt_breakdown(s, room, from_date, to_date)
            want = _table_breakdown(s, room, from_date, to_date)
            assert got == want, (seed, from_date, to_date)
            assert ledger.period_transfers(s, room, from_date, to_date) == net_transfers(want)
