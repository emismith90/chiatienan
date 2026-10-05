"""Release B review fixes: rolling back to the tables-only commit and forward again
heals the journal on the next deploy (B#1); a value of the wrong type cannot make the
journal disagree with its table (B#2); a journal that cannot be read back is refused
with its reason, not a crash (B#3)."""
from datetime import date

from app import ledger, migrate_storage
from ledger_core import importer
from ledger_core.journal import append_raw, unmirrored
from ledger_core.models import Meal, MealShare
from ledger_core.view import LedgerView
from packs.poker_ledger import importer as poker_import
from packs.poker_ledger.models import Game, GameEntry
from tests.test_ledger import _seed_room


def _meal(s, room, payer, who, amount, day):
    return ledger.record_meal(s, room_id=room, payer_member_id=payer, participants=who,
                              total_amount=amount, occurred_on=date(2026, 9, day))["meal_id"]


def test_the_deploy_after_a_rollback_reconciles_the_journal_with_the_tables(db):
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        mid = _meal(s, room, a, [a, b], 100_000, 1)
        pid = ledger.record_payment(s, room_id=room, from_member_id=b, to_member_id=a, amount=50_000,
                                    occurred_on=date(2026, 9, 2), meal_id=mid)["payment_id"]
        g = Game(room_id=room, played_on=date(2026, 9, 5), house=0)
        g.entries = [GameEntry(member_id=a, buy_in=100, cash_out=150), GameEntry(member_id=b, buy_in=100, cash_out=50)]
        s.add(g)
        s.flush()
        gid = g.id
    assert migrate_storage.run(db, apply=True)[0] == 0
    assert migrate_storage.pending(db) is None

    # The previous commit serves for a while: it writes the tables only.
    with unmirrored(), db.session() as s:
        new_mid = _meal(s, room, b, [a, b], 60_000, 3)
        ledger.void_meal(s, mid, room_id=room, by="x")               # also untargets the payment
        s.get(Game, gid).voided = True
    assert "disagree" in migrate_storage.pending(db)

    status, report = migrate_storage.run(db, apply=True)               # the roll-forward deploy
    assert status == 0 and report["ledger_step"]["status"] == "reconciled", report
    assert report["ledger_step"]["reconciled"] == {f"ledger:{room}": 3, f"games:{room}": 1}
    assert migrate_storage.pending(db) is None
    with db.session() as s:
        assert importer.differences(s, room) == [] and poker_import.differences(s, room) == []
        view = LedgerView(s, room)
        assert view.meal(mid).voided and view.meal(new_mid).total_amount == 60_000
        assert view.payment(pid).meal_id is None
    assert migrate_storage.run(db, apply=True) == (0, {"status": "already applied"})


def test_a_value_of_the_wrong_type_is_journaled_as_the_table_stores_it(db):
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        m = Meal(room_id=room, occurred_on=date(2026, 9, 1), payer_member_id=a, total_amount=10,
                 place_id="7", note=5, dish="phở")
        m.shares = [MealShare(member_id=a, share_amount=5), MealShare(member_id=b, share_amount="5")]
        s.add(m)
    with db.session() as s:
        assert importer.differences(s, room) == []
        [rec] = LedgerView(s, room).meals()
        assert rec.place_id == 7 and rec.note == "5" and rec.shares[1].share_amount == 5


def test_a_journal_that_cannot_be_read_back_is_refused_with_its_reason(db):
    room, (a, b) = _seed_room(db, 2)
    with db.session() as s:
        _meal(s, room, a, [a, b], 100_000, 1)
    assert migrate_storage.run(db, apply=True)[0] == 0
    with db.session() as s:
        append_raw(s, "ledger", room, {"event": "meal_void", "meal_id": 999, "voided": True})
    reason = migrate_storage.pending(db)
    assert "does not start at the beginning" in reason
    status, report = migrate_storage.run(db, apply=True)
    assert status == 1 and report["ledger_step"]["status"].startswith("refused")
