from datetime import date

import pytest

from app import ledger, roster
from app.money import MoneyError


def _seed_members(session, n):
    return [
        roster.create_member(
            session,
            display_name=f"M{i}",
            bank_code="VCB",
            account_number=f"00{i}",
            account_holder=f"M{i}",
        )
        for i in range(1, n + 1)
    ]


def test_record_meal_writes_shares_summing_to_total(db):
    with db.session() as s:
        m = _seed_members(s, 3)
        res = ledger.record_meal(
            s,
            payer_member_id=m[0].id,
            participants=[x.id for x in m],
            total_amount=600,
            occurred_on=date(2026, 7, 15),
        )
        assert res["meal_id"] > 0
        assert sum(res["shares"].values()) == 600


def test_record_meal_rejects_unknown_participant(db):
    with db.session() as s:
        m = _seed_members(s, 2)
        with pytest.raises(ledger.LedgerError):
            ledger.record_meal(
                s, payer_member_id=m[0].id, participants=[m[0].id, 9999], total_amount=100
            )


def test_record_meal_rejects_bad_split(db):
    with db.session() as s:
        m = _seed_members(s, 2)
        with pytest.raises(MoneyError):
            ledger.record_meal(
                s, payer_member_id=m[0].id, participants=[x.id for x in m], total_amount=0
            )


def test_balances_paid_minus_consumed(db):
    with db.session() as s:
        a, b, c = _seed_members(s, 3)
        # A pays 900 for all three (300 each)
        ledger.record_meal(
            s,
            payer_member_id=a.id,
            participants=[a.id, b.id, c.id],
            total_amount=900,
            occurred_on=date(2026, 7, 15),
        )
        bal = ledger.period_balances(s, date(2026, 7, 1), date(2026, 7, 31))
        assert bal[a.id]["balance"] == 600   # paid 900, consumed 300
        assert bal[b.id]["balance"] == -300
        assert bal[c.id]["balance"] == -300
        assert sum(v["balance"] for v in bal.values()) == 0


def test_payer_not_participant_balance(db):
    with db.session() as s:
        a, b, c = _seed_members(s, 3)
        # A pays 200 but doesn't eat; B & C split
        ledger.record_meal(
            s,
            payer_member_id=a.id,
            participants=[b.id, c.id],
            total_amount=200,
            occurred_on=date(2026, 7, 15),
        )
        bal = ledger.period_balances(s, date(2026, 7, 1), date(2026, 7, 31))
        assert bal[a.id]["balance"] == 200
        assert bal[a.id]["consumed"] == 0
        assert bal[b.id]["balance"] == -100


def test_voided_meal_excluded_from_balances(db):
    with db.session() as s:
        a, b = _seed_members(s, 2)
        res = ledger.record_meal(
            s, payer_member_id=a.id, participants=[a.id, b.id], total_amount=200,
            occurred_on=date(2026, 7, 15),
        )
        ledger.void_meal(s, res["meal_id"], by="tester")
        bal = ledger.period_balances(s, date(2026, 7, 1), date(2026, 7, 31))
        assert all(v["balance"] == 0 for v in bal.values()) or bal == {}


def test_void_unknown_meal_raises(db):
    with db.session() as s:
        with pytest.raises(ledger.LedgerError):
            ledger.void_meal(s, 424242)


def test_since_last_window_uses_last_settlement(db):
    with db.session() as s:
        a, b = _seed_members(s, 2)
        # meal before settlement
        ledger.record_meal(
            s, payer_member_id=a.id, participants=[a.id, b.id], total_amount=200,
            occurred_on=date(2026, 7, 10),
        )
        ledger.record_settlement(
            s, period_from=None, period_to=date(2026, 7, 13), requested_by="a", transfers=[]
        )
        # meal after settlement
        ledger.record_meal(
            s, payer_member_id=b.id, participants=[a.id, b.id], total_amount=200,
            occurred_on=date(2026, 7, 15),
        )
        last = ledger.last_settlement(s)
        assert last.period_to == date(2026, 7, 13)
        # window since last: 07-14 .. 07-31 → only the second meal counts
        bal = ledger.period_balances(s, date(2026, 7, 14), date(2026, 7, 31))
        assert bal[b.id]["balance"] == 100   # b paid 200, consumed 100
        assert bal[a.id]["balance"] == -100


def test_idempotency_marks_activity(db):
    with db.session() as s:
        assert ledger.already_processed(s, "act-1") is False
        ledger.mark_processed(s, "act-1")
    with db.session() as s:
        assert ledger.already_processed(s, "act-1") is True
