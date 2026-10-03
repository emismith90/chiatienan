"""The ledger journal: every money-row change mirrored as an event, and the guard
(plan 2026-10-02, release B, B1/B2)."""
import json
from datetime import date

import pytest
from sqlalchemy import text

from app import ledger
from ledger_core.journal import LedgerImmutable
from ledger_core.models import Meal, MealShare, Payment
from tests.test_ledger import _seed_room


def _events(db, room_id):
    with db.engine.connect() as c:
        rows = c.execute(text(
            "SELECT d.doc_id, d.data FROM kn_documents d JOIN kn_collections c ON c.id = d.collection_id "
            "WHERE c.slug = 'ledger' AND d.space_id = :s ORDER BY d.doc_id"), {"s": str(room_id)}).fetchall()
    return [json.loads(r[1]) for r in rows]


def test_a_recorded_meal_is_one_event_with_its_shares(db):
    room, (a, b, c) = _seed_room(db, 3)
    with db.session() as s:
        res = ledger.record_meal(s, room_id=room, payer_member_id=a, participants=[a, b, c],
                                 total_amount=300_000, occurred_on=date(2026, 9, 1), dish="phở")
    [ev] = _events(db, room)
    assert ev["event"] == "meal" and ev["id"] == res["meal_id"] and ev["total_amount"] == 300_000
    assert ev["occurred_on"] == "2026-09-01" and ev["dish"] == "phở" and ev["voided"] is False
    assert sorted((sh["member_id"], sh["share_amount"]) for sh in ev["shares"]) == [(a, 100_000), (b, 100_000), (c, 100_000)]
    assert " " in ev["created_at"] and "+" not in ev["created_at"]    # naive local, as the table stores it


def test_voids_and_retargets_are_their_own_events(db):
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        mid = ledger.record_meal(s, room_id=room, payer_member_id=a, participants=[a, b],
                                 total_amount=100_000, occurred_on=date(2026, 9, 1))["meal_id"]
        pid = ledger.record_payment(s, room_id=room, from_member_id=b, to_member_id=a, amount=50_000,
                                    occurred_on=date(2026, 9, 2), meal_id=mid)["payment_id"]
    with db.session() as s:
        ledger.void_meal(s, mid, room_id=room, by="7")             # also untargets the payment
    with db.session() as s:
        ledger.void_payment(s, pid, room_id=room, by="7")
    kinds = [(e["event"], e.get("meal_id"), e.get("payment_id")) for e in _events(db, room)]
    assert kinds[:2] == [("meal", None, None), ("payment", mid, None)]      # full rows carry `id`
    assert ("meal_void", mid, None) in kinds and ("payment_retarget", None, pid) in kinds
    assert kinds[-1] == ("payment_void", None, pid)
    void = next(e for e in _events(db, room) if e["event"] == "meal_void")
    assert void["voided"] is True and void["voided_by"] == "7" and void["voided_at"]


def test_any_other_change_to_a_money_row_is_refused_and_writes_nothing(db):
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        mid = ledger.record_meal(s, room_id=room, payer_member_id=a, participants=[a, b],
                                 total_amount=100_000, occurred_on=date(2026, 9, 1))["meal_id"]
    with pytest.raises(LedgerImmutable, match="total_amount cannot change"):
        with db.session() as s:
            s.get(Meal, mid).total_amount = 1
    with db.session() as s:
        assert s.get(Meal, mid).total_amount == 100_000
    assert len(_events(db, room)) == 1


def test_a_money_row_cannot_be_deleted_or_its_split_changed(db):
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        mid = ledger.record_meal(s, room_id=room, payer_member_id=a, participants=[a, b],
                                 total_amount=100_000, occurred_on=date(2026, 9, 1))["meal_id"]
    with pytest.raises(LedgerImmutable, match="cannot be deleted"):
        with db.session() as s:
            s.delete(s.get(Meal, mid))
    with pytest.raises(LedgerImmutable, match="shares are fixed"):
        with db.session() as s:
            s.add(MealShare(meal_id=mid, member_id=b, share_amount=1))


def test_a_rolled_back_write_leaves_no_event(db):
    room, (a, b) = _seed_room(db, 2)
    with pytest.raises(RuntimeError):
        with db.session() as s:
            ledger.record_meal(s, room_id=room, payer_member_id=a, participants=[a, b],
                               total_amount=100_000, occurred_on=date(2026, 9, 1))
            raise RuntimeError("the card failed")
    assert _events(db, room) == []


def test_events_number_per_room(db):
    r1, (a, b) = _seed_room(db, 2, token="t1")
    r2, (c, d) = _seed_room(db, 2, token="t2")
    with db.session() as s:
        ledger.record_meal(s, room_id=r1, payer_member_id=a, participants=[a, b], total_amount=2, occurred_on=date(2026, 9, 1))
        ledger.record_meal(s, room_id=r2, payer_member_id=c, participants=[c, d], total_amount=2, occurred_on=date(2026, 9, 1))
    with db.engine.connect() as conn:
        assert sorted(conn.execute(text("SELECT space_id, doc_id FROM kn_documents d JOIN kn_collections c "
                                        "ON c.id = d.collection_id WHERE c.slug = 'ledger'")).fetchall()) == \
            [(str(r1), "000001"), (str(r2), "000001")]


def test_a_settlement_is_an_event(db):
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_settlement(s, room_id=room, period_from=None, period_to=date(2026, 9, 30),
                                 transfers=[{"from": b, "to": a, "amount": 50_000}], requested_by="1")
    [ev] = _events(db, room)
    assert ev["event"] == "settlement" and ev["period_to"] == "2026-09-30" and ev["period_from"] is None
    assert ev["transfers"] == [{"from": b, "to": a, "amount": 50_000}]


def test_a_poker_game_and_its_void_go_to_the_packs_own_journal(db):
    from packs.poker_ledger.models import Game, GameEntry
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        g = Game(room_id=room, played_on=date(2026, 9, 5), house=10_000)
        g.entries = [GameEntry(member_id=a, buy_in=200_000, cash_out=350_000),
                     GameEntry(member_id=b, buy_in=200_000, cash_out=40_000)]
        s.add(g)
        s.flush()
        gid = g.id
    with db.session() as s:
        game = s.get(Game, gid)
        game.voided, game.voided_by = True, "1"
    with pytest.raises(LedgerImmutable, match="house cannot change"):
        with db.session() as s:
            s.get(Game, gid).house = 0
    with db.engine.connect() as c:
        evs = [json.loads(r[0]) for r in c.execute(text(
            "SELECT d.data FROM kn_documents d JOIN kn_collections c ON c.id = d.collection_id "
            "WHERE c.slug = 'games' AND d.space_id = :s ORDER BY d.doc_id"), {"s": str(room)})]
    assert [e["event"] for e in evs] == ["game", "game_void"]
    assert [(x["member_id"], x["buy_in"], x["cash_out"]) for x in evs[0]["entries"]] == \
        [(a, 200_000, 350_000), (b, 200_000, 40_000)]
    assert _events(db, room) == []                      # nothing poker in the ledger journal
