from datetime import date

import pytest

from app import ledger
from app.money import MoneyError
from app.models import Member, Room


def _seed_room(db, n, *, token="tok"):
    """Create a room with ``n`` members directly (bypasses the stale roster
    module, which still references pre-task-1 Member columns).

    Returns ``(room_id, [member_id, ...])``.
    """
    with db.session() as s:
        room = Room(name="Room", invite_token=token)
        s.add(room)
        s.flush()
        members = [
            Member(room_id=room.id, display_name=f"M{i}", nickname=f"m{i}", pin=str(i))
            for i in range(1, n + 1)
        ]
        s.add_all(members)
        s.flush()
        return room.id, [m.id for m in members]


def test_record_meal_writes_shares_summing_to_total(db):
    room_id, m = _seed_room(db, 3)
    with db.session() as s:
        res = ledger.record_meal(
            s,
            room_id=room_id,
            payer_member_id=m[0],
            participants=m,
            total_amount=600,
            occurred_on=date(2026, 7, 15),
        )
        assert res["meal_id"] > 0
        assert sum(res["shares"].values()) == 600


def test_record_meal_rejects_unknown_participant(db):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            ledger.record_meal(
                s, room_id=room_id, payer_member_id=m[0],
                participants=[m[0], 9999], total_amount=100,
            )


def test_record_meal_rejects_bad_split(db):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        with pytest.raises(MoneyError):
            ledger.record_meal(
                s, room_id=room_id, payer_member_id=m[0], participants=m, total_amount=0
            )


def test_balances_paid_minus_consumed(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        # A pays 900 for all three (300 each)
        ledger.record_meal(
            s,
            room_id=room_id,
            payer_member_id=a,
            participants=[a, b, c],
            total_amount=900,
            occurred_on=date(2026, 7, 15),
        )
        bal = ledger.period_balances(s, room_id, date(2026, 7, 1), date(2026, 7, 31))
        assert bal[a]["balance"] == 600   # paid 900, consumed 300
        assert bal[b]["balance"] == -300
        assert bal[c]["balance"] == -300
        assert sum(v["balance"] for v in bal.values()) == 0


def test_payer_not_participant_balance(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        # A pays 200 but doesn't eat; B & C split
        ledger.record_meal(
            s,
            room_id=room_id,
            payer_member_id=a,
            participants=[b, c],
            total_amount=200,
            occurred_on=date(2026, 7, 15),
        )
        bal = ledger.period_balances(s, room_id, date(2026, 7, 1), date(2026, 7, 31))
        assert bal[a]["balance"] == 200
        assert bal[a]["consumed"] == 0
        assert bal[b]["balance"] == -100


def test_voided_meal_excluded_from_balances(db):
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        res = ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b], total_amount=200,
            occurred_on=date(2026, 7, 15),
        )
        ledger.void_meal(s, res["meal_id"], room_id=room_id, by="tester")
        bal = ledger.period_balances(s, room_id, date(2026, 7, 1), date(2026, 7, 31))
        assert all(v["balance"] == 0 for v in bal.values()) or bal == {}


def test_void_unknown_meal_raises(db):
    room_id, _ = _seed_room(db, 1)
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            ledger.void_meal(s, 424242, room_id=room_id)


def test_void_rejects_meal_from_other_room(db):
    room_id, (a, b) = _seed_room(db, 2, token="tok-a")
    other_room_id, _ = _seed_room(db, 1, token="tok-b")
    with db.session() as s:
        res = ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b], total_amount=200,
            occurred_on=date(2026, 7, 15),
        )
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            ledger.void_meal(s, res["meal_id"], room_id=other_room_id)


def test_since_last_window_uses_last_settlement(db):
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        # meal before settlement
        ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b], total_amount=200,
            occurred_on=date(2026, 7, 10),
        )
        ledger.record_settlement(
            s, room_id=room_id, period_from=None, period_to=date(2026, 7, 13),
            requested_by="a", transfers=[],
        )
        # meal after settlement
        ledger.record_meal(
            s, room_id=room_id, payer_member_id=b, participants=[a, b], total_amount=200,
            occurred_on=date(2026, 7, 15),
        )
        last = ledger.last_settlement(s, room_id)
        assert last.period_to == date(2026, 7, 13)
        # window since last: 07-14 .. 07-31 → only the second meal counts
        bal = ledger.period_balances(s, room_id, date(2026, 7, 14), date(2026, 7, 31))
        assert bal[b]["balance"] == 100   # b paid 200, consumed 100
        assert bal[a]["balance"] == -100


def test_record_and_balances_are_room_scoped(db):
    room_id, (an, bi) = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=an,
                            participants=[an, bi], total_amount=100000)
    with db.session() as s:
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        assert bal[an]["balance"] == 50000 and bal[bi]["balance"] == -50000
        # other room sees nothing
        assert ledger.period_balances(s, 999, None, date(2999, 1, 1)) == {}


def test_record_meal_with_guest_tracks_members_only(db):
    room_id, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        res = ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b, c],
            total_amount=400_000, guests=["Emi"], occurred_on=date(2026, 7, 15),
        )
        assert res["bill_total"] == 400_000
        assert res["tracked_total"] == 300_000
        assert res["total_amount"] == 300_000       # persisted total = tracked
        assert res["guests"] == ["Emi"]
        assert sum(res["shares"].values()) == 300_000
        bal = ledger.period_balances(s, room_id, date(2026, 7, 1), date(2026, 7, 31))
        assert bal[a]["balance"] == 200_000          # paid 300k, consumed 100k
        assert bal[b]["balance"] == -100_000
        assert bal[c]["balance"] == -100_000


def test_record_payment_shifts_balances(db):
    from datetime import date
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0],
                           participants=[m[0], m[1]], total_amount=200,
                           occurred_on=date(2026, 7, 20))
        # m0 +100, m1 -100
        ledger.record_payment(s, room_id=room_id, from_member_id=m[1],
                              to_member_id=m[0], amount=40, occurred_on=date(2026, 7, 20))
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        assert bal[m[0]]["balance"] == 60   # 100 - 40 received
        assert bal[m[1]]["balance"] == -60  # -100 + 40 paid
        assert bal[m[0]]["balance"] + bal[m[1]]["balance"] == 0


def test_record_payment_payment_only_member_appears(db):
    """A payment with no meal behind it still puts both members in the period
    view, but it is not a balance.

    `balance` is a debt position derived from the meal edges, so cash that
    settles nothing reads as 0 rather than an open credit. That is the same rule
    `DebtEdge.outstanding` follows (it never goes negative), and treating a
    payment as a standalone credit is exactly what let a bounded window report
    debts nobody held.
    """
    from datetime import date
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                              to_member_id=m[1], amount=50, occurred_on=date(2026, 7, 20))
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        assert set(bal) == {m[0], m[1]}
        assert bal[m[0]]["balance"] == 0
        assert bal[m[1]]["balance"] == 0


def test_record_payment_validation(db):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                  to_member_id=m[0], amount=10)  # from == to
        with pytest.raises(ledger.LedgerError):
            ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                  to_member_id=m[1], amount=0)   # amount <= 0
        with pytest.raises(ledger.LedgerError):
            ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                  to_member_id=9999, amount=10)  # unknown member


def test_voided_payment_excluded_from_balances(db):
    from datetime import date
    from app.models import Payment
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        res = ledger.record_payment(s, room_id=room_id, from_member_id=m[0],
                                    to_member_id=m[1], amount=50, occurred_on=date(2026, 7, 20))
        s.get(Payment, res["payment_id"]).voided = True
        s.flush()
        bal = ledger.period_balances(s, room_id, None, date(2999, 1, 1))
        assert bal.get(m[0], {"balance": 0})["balance"] == 0
        assert bal.get(m[1], {"balance": 0})["balance"] == 0


def test_record_meal_stores_metadata(db):
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        res = ledger.record_meal(
            s, room_id=room_id, payer_member_id=a, participants=[a, b],
            total_amount=200_000, dish="phở", initiator="Emi",
            note="An đổi ý", raw_input="@phoenix 200k phở",
        )
        meal = s.get(__import__("app.models", fromlist=["Meal"]).Meal, res["meal_id"])
        assert meal.dish == "phở" and meal.initiator == "Emi"
        assert meal.note == "An đổi ý" and meal.raw_input == "@phoenix 200k phở"
        assert meal.guests == []


# --- window semantics: meals are windowed, payment attribution is not -------- #

def _pair_meal_then_late_payment(db):
    """Meal on the 23rd, repaid on the 27th — the room's normal rhythm."""
    from datetime import date
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        meal = ledger.record_meal(
            s, room_id=room_id, payer_member_id=b, participants=[a, b],
            total_amount=80_000, adjustments={}, guests=[], dish="bún chả",
            occurred_on=date(2026, 7, 23), logged_by=str(b),
        )
        ledger.record_payment(
            s, room_id=room_id, from_member_id=a, to_member_id=b,
            amount=meal["shares"][a], occurred_on=date(2026, 7, 27), logged_by=str(a),
        )
    return room_id, a, b


def test_a_debt_repaid_after_the_window_is_not_outstanding_again(db):
    """Production: "chốt tuần trước" asked on Monday billed the 107,000đ that had
    been paid that same morning, and printed a live QR for it."""
    from datetime import date
    room_id, a, b = _pair_meal_then_late_payment(db)
    with db.session() as s:
        # Window covers the meal but ends before the repayment.
        edges = ledger.debt_breakdown(s, room_id, date(2026, 7, 20), date(2026, 7, 26))
        assert [e.outstanding for e in edges] == [0]
        assert ledger.period_transfers(s, room_id, date(2026, 7, 20), date(2026, 7, 26)) == []


def test_the_window_still_excludes_meals_outside_it(db):
    from datetime import date
    room_id, a, b = _pair_meal_then_late_payment(db)
    with db.session() as s:
        # A window after the meal sees no edges at all.
        assert ledger.debt_breakdown(s, room_id, date(2026, 7, 24), date(2026, 7, 27)) == []


def test_period_balances_agree_with_the_edges_on_a_bounded_window(db):
    """`balance` used to fold in every payment dated in the window regardless of
    which meal it settled, so a bounded window reported debts nobody held."""
    from datetime import date
    room_id, a, b = _pair_meal_then_late_payment(db)
    with db.session() as s:
        # The payment lands in this window; the meal it settles does not.
        bal = ledger.period_balances(s, room_id, date(2026, 7, 27), date(2026, 7, 27))
        assert all(v["balance"] == 0 for v in bal.values()), bal
        # And over the meal's own window it is settled, so also zero.
        bal2 = ledger.period_balances(s, room_id, date(2026, 7, 20), date(2026, 7, 26))
        assert all(v["balance"] == 0 for v in bal2.values()), bal2


def test_paid_and_consumed_still_describe_the_window(db):
    """Only `balance` changed meaning — cash fronted and food eaten are per-window."""
    from datetime import date
    room_id, a, b = _pair_meal_then_late_payment(db)
    with db.session() as s:
        bal = ledger.period_balances(s, room_id, date(2026, 7, 23), date(2026, 7, 23))
        assert bal[b]["paid"] == 80_000
        assert bal[a]["consumed"] == 40_000


def test_voiding_a_meal_untargets_its_payments(db):
    from datetime import date
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        meal = ledger.record_meal(
            s, room_id=room_id, payer_member_id=b, participants=[a, b],
            total_amount=100_000, adjustments={}, guests=[], dish="pho",
            occurred_on=date(2026, 7, 23), logged_by=str(b),
        )
        ledger.record_payment(
            s, room_id=room_id, from_member_id=a, to_member_id=b, amount=50_000,
            meal_id=meal["meal_id"], occurred_on=date(2026, 7, 23), logged_by=str(a),
        )
        out = ledger.void_meal(s, meal["meal_id"], room_id=room_id, by=str(b))
        assert out["payments_untargeted"] == 1
        from app.models import Payment
        from sqlalchemy import select as _select
        assert s.scalars(_select(Payment).where(Payment.room_id == room_id)).first().meal_id is None


# --- outstanding_pairs: who owes whom, both directions kept ------------------ #

def test_outstanding_pairs_sums_a_pair_over_meals_without_netting_directions(db):
    """The group-wide owe/owed view that replaced the per-person balance bars.

    A owes B for two meals and B owes A for one. Three edges, two directions,
    two rows — nothing collapses into a single signed number. Netting belongs to
    period_transfers, and only so a settlement can print one QR.
    """
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=a, participants=[a, b],
                           total_amount=100_000, occurred_on=date(2026, 7, 21))
        ledger.record_meal(s, room_id=room_id, payer_member_id=a, participants=[a, b],
                           total_amount=60_000, occurred_on=date(2026, 7, 22))
        ledger.record_meal(s, room_id=room_id, payer_member_id=b, participants=[a, b],
                           total_amount=40_000, occurred_on=date(2026, 7, 23))
        rows = ledger.outstanding_pairs(s, room_id, None, date(2026, 7, 31))

    assert rows == [
        {"debtor_id": b, "creditor_id": a, "amount": 80_000},   # 50k + 30k
        {"debtor_id": a, "creditor_id": b, "amount": 20_000},
    ]


def test_outstanding_pairs_drops_a_debt_once_it_is_paid(db):
    room_id, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        meal = ledger.record_meal(s, room_id=room_id, payer_member_id=a,
                                  participants=[a, b], total_amount=100_000,
                                  occurred_on=date(2026, 7, 21))
        assert ledger.outstanding_pairs(s, room_id, None, date(2026, 7, 31))
        ledger.record_payment(s, room_id=room_id, from_member_id=b, to_member_id=a,
                              amount=meal["shares"][b], logged_by=str(b))
        assert ledger.outstanding_pairs(s, room_id, None, date(2026, 7, 31)) == []
