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
