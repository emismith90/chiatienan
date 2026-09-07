"""Balances from pack contributions (plan Task 3.4, review F4).

`debt_breakdown` sums the debt edges of every registered source, applies payments
FIFO over that one list and windows afterwards. With the lunch pack registered the
numbers over the golden week are the ones the inline meal query produced; with a
second source registered its edges take part — which is what makes a second money
business possible without the core learning its tables.
"""
from datetime import date
from types import SimpleNamespace

from app import ledger
from app.packs import lunch_ledger_pack
from bench.world import build_world
from ledger_core import ledger as core_ledger
from ledger_core.money import DebtEdge, apply_payments_fifo, net_transfers
from tests.golden.scenario_week import MEMBERS, STEPS

TO = date(2026, 7, 27)


def _golden_world(db):
    case = SimpleNamespace(source="golden", id="week", members=MEMBERS, prior_steps=STEPS)
    room_id, ids, _drafts = build_world(db, case)
    return room_id, ids


def _with_sources(sources, fn):
    saved = core_ledger._edge_sources
    core_ledger.set_edge_sources(sources)
    try:
        return fn()
    finally:
        core_ledger.set_edge_sources(saved)


def test_breakdown_over_the_golden_week_is_unchanged_by_the_seam(db):
    room_id, ids = _golden_world(db)
    with db.session() as s:
        # the pre-3.4 computation, inlined: meals → gross edges → FIFO → window
        payments = [{"from": p.from_member_id, "to": p.to_member_id, "amount": p.amount, "meal_id": p.meal_id}
                    for p in s.query(ledger.Payment).filter_by(room_id=room_id, voided=False)]
        inline = [e for e in apply_payments_fifo(core_ledger.meal_edges(s, room_id), payments) if e.occurred_on <= TO]
        default = _with_sources(None, lambda: ledger.debt_breakdown(s, room_id, None, TO))
        via_pack = _with_sources([lunch_ledger_pack().contributions], lambda: ledger.debt_breakdown(s, room_id, None, TO))
    assert inline and inline == default == via_pack
    assert net_transfers(via_pack) == net_transfers(inline)


def test_a_second_source_takes_part_in_balances_and_transfers(db):
    room_id, ids = _golden_world(db)
    extra = DebtEdge(debtor=ids["a3"], creditor=ids["a1"], meal_id=-1, dish="poker night",
                     occurred_on=date(2026, 7, 26), amount=1_000_000)

    def poker(session, space_id):
        return [extra] if space_id == room_id else []

    with db.session() as s:
        before = _with_sources([lunch_ledger_pack().contributions], lambda: ledger.debt_breakdown(s, room_id, None, TO))
        after = _with_sources([lunch_ledger_pack().contributions, poker], lambda: ledger.debt_breakdown(s, room_id, None, TO))
        assert extra in after and extra not in before and len(after) == len(before) + 1
        owed_before = sum(t.amount for t in net_transfers(before) if t.to_member == ids["a1"])
        owed_after = sum(t.amount for t in net_transfers(after) if t.to_member == ids["a1"])
        assert owed_after == owed_before + 1_000_000
        # windowing happens after FIFO, on the summed list: a window that ends before
        # the poker night never sees its edge, whatever source it came from
        early = _with_sources([lunch_ledger_pack().contributions, poker],
                              lambda: ledger.debt_breakdown(s, room_id, None, date(2026, 7, 25)))
        assert extra not in early
        balances = _with_sources([lunch_ledger_pack().contributions, poker],
                                 lambda: ledger.period_balances(s, room_id, None, TO))
        assert balances[ids["a3"]]["balance"] == -(1_000_000 + sum(
            e.outstanding for e in before if e.debtor == ids["a3"]))


def test_the_kernel_registers_its_packs_as_the_edge_sources(db):
    from app.kernel import kernel_for
    k = kernel_for(db)
    assert [f.__self__.id for f in core_ledger._edge_sources] == [p.id for p in k.packs.list()]
