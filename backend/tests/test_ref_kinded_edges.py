"""Debt edges are keyed by what they reference (plan Task 6.1, review F1): a meal #N and
a game-shaped edge #N between the same two people never share a payment, whichever
sources are registered; and the period timeline is the registered sources' events."""
from datetime import date

from app import ledger
from ledger_core import ledger as core_ledger
from ledger_core.money import DebtEdge, apply_payments_fifo
from tests.test_ledger import _seed_room


def test_targeted_payment_settles_only_the_edge_of_its_kind():
    d = date(2026, 7, 20)
    meal = DebtEdge(debtor=2, creditor=1, meal_id=5, dish="bún", occurred_on=d, amount=100_000)
    game = DebtEdge(debtor=2, creditor=1, meal_id=5, dish="game #5", occurred_on=d, amount=300_000, ref_kind="game")
    assert meal.ref_id == 5 and game.label == "game #5" and meal.ref_kind == "meal"
    out = apply_payments_fifo([game, meal], [{"from": 2, "to": 1, "amount": 100_000, "meal_id": 5}])
    by_kind = {e.ref_kind: e for e in out}
    assert by_kind["meal"].paid == 100_000 and by_kind["game"].paid == 0          # the meal, never the game
    out = apply_payments_fifo([game, meal], [{"from": 2, "to": 1, "amount": 100_000, "meal_id": 5, "ref_kind": "game"}])
    by_kind = {e.ref_kind: e for e in out}
    assert by_kind["game"].paid == 100_000 and by_kind["meal"].paid == 0
    # an untargeted payment still pools oldest-first; on one day the order is by kind name
    # then id — deterministic, whichever source registered first
    out = apply_payments_fifo([meal, game], [{"from": 2, "to": 1, "amount": 350_000}])
    by_kind = {e.ref_kind: e for e in out}
    assert by_kind["game"].paid == 300_000 and by_kind["meal"].paid == 50_000
    assert apply_payments_fifo([game, meal], [{"from": 2, "to": 1, "amount": 350_000}]) == out


def test_a_registered_game_source_shares_a_room_with_meals_safely(db):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0], participants=m, total_amount=200_000,
                           occurred_on=date(2026, 7, 20), logged_by="t")
    game_edges = [DebtEdge(debtor=m[1], creditor=m[0], meal_id=1, dish="game #1", occurred_on=date(2026, 7, 20),
                           amount=400_000, ref_kind="game")]
    saved = core_ledger._edge_sources
    core_ledger.set_edge_sources([core_ledger.meal_edges, lambda s, sid: game_edges if sid == room_id else []])
    try:
        with db.session() as s:
            ledger.record_payment(s, room_id=room_id, from_member_id=m[1], to_member_id=m[0], amount=100_000,
                                  occurred_on=date(2026, 7, 21), meal_id=1, logged_by="t")   # targets meal #1
            edges = {e.ref_kind: e for e in ledger.debt_breakdown(s, room_id, None, date(2026, 7, 31))}
            assert edges["meal"].paid == 100_000 and edges["game"].paid == 0
            stmt = ledger.statement_for(s, room_id, m[1], None, date(2026, 7, 31))
            assert sorted(r["dish"] for r in stmt["owe"]) == ["game #1"] or sorted(r["dish"] for r in stmt["owe"]) == ["game #1", None]
    finally:
        core_ledger.set_edge_sources(saved)


def test_period_timeline_is_the_registered_sources_plus_payments(db):
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        ledger.record_meal(s, room_id=room_id, payer_member_id=m[0], participants=m, total_amount=200_000,
                           occurred_on=date(2026, 7, 20), logged_by="t")
        ledger.record_payment(s, room_id=room_id, from_member_id=m[1], to_member_id=m[0], amount=50_000,
                              occurred_on=date(2026, 7, 21), logged_by="t")
    game = {"kind": "game", "game_id": 9, "occurred_on": "2026-07-19", "created_at": "", "pot": 1_000_000}
    saved = core_ledger._timeline_sources
    try:
        core_ledger.set_timeline_sources([core_ledger.meal_timeline, lambda s, sid, a, b: [game]])
        with db.session() as s:
            kinds = [e["kind"] for e in ledger.period_timeline(s, room_id, None, date(2026, 7, 31))]
        assert kinds == ["game", "meal", "payment"]
        core_ledger.set_timeline_sources([lambda s, sid, a, b: [game]])          # a business without meals
        with db.session() as s:
            assert [e["kind"] for e in ledger.period_timeline(s, room_id, None, date(2026, 7, 31))] == ["game", "payment"]
    finally:
        core_ledger.set_timeline_sources(saved)
    from packs.ledger_tools.render import _summary_body
    body = _summary_body({"period": {"from": None, "to": "2026-07-31"},
                          "timeline": [game, {"kind": "payment", "occurred_on": "2026-07-21"}]})
    assert body == "Summary through 2026-07-31: 1 payment, 1 game across 2 days — details below."
