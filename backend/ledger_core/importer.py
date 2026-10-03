"""Import the ledger tables into the journal once, and check the two agree
(plan 2026-10-02, release B, B5).

:func:`import_room` writes each existing meal (with its shares), payment and settlement
as one event carrying the row's current state — voids, re-pointed payments and place
links included — through the same serializers the live mirror uses.

:func:`differences` is the parity check, and it reads the tables by a **separate**
path: ORM rows straight into the view's records, never through JSON. A serialization
bug therefore cannot cancel itself out between the two sides. Run by the migration
before it commits, by ``migrate_storage --parity``, and at startup (which refuses to
boot on any difference). :func:`reconcile` repairs a journal the tables moved ahead of.
"""
from __future__ import annotations

from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger_core import journal
from ledger_core.models import Meal, Payment, Settlement
from ledger_core.view import LedgerView, MealRecord, PaymentRecord, SettlementRecord, ShareRecord


def ledger_rooms(session: Session) -> list[int]:
    rooms: set[int] = set()
    for model in (Meal, Payment, Settlement):
        rooms |= set(session.scalars(select(model.room_id).distinct()).all())
    return sorted(rooms)


def journal_size(session: Session, room_id: int) -> int:
    from ledger_core.view import journal_events
    return len(journal_events(session, "ledger", room_id))


def import_room(session: Session, room_id: int) -> int:
    """Append one event per existing row of ``room_id``, oldest first. Returns the count."""
    rows: list[tuple] = []
    for m in session.scalars(select(Meal).where(Meal.room_id == room_id)):
        rows.append((m.created_at, 0, m.id, journal._meal_inserted(m)[0]))
    for p in session.scalars(select(Payment).where(Payment.room_id == room_id)):
        rows.append((p.created_at, 1, p.id, journal._payment_inserted(p)[0]))
    for st in session.scalars(select(Settlement).where(Settlement.room_id == room_id)):
        rows.append((st.created_at, 2, st.id, journal._settlement_inserted(st)[0]))
    rows.sort(key=lambda r: (r[0] is None, r[0] or 0, r[1], r[2]))
    for _when, _kind, _id, ev in rows:
        ev.pop("_order", None)
        journal.append_raw(session, "ledger", room_id, ev)
    return len(rows)


# ---------------------------------------------------------------------- parity

def table_records(session: Session, room_id: int):
    """``(meals, payments, settlements)`` read from the tables, as records, by id."""
    meals = [MealRecord(
        id=m.id, room_id=m.room_id, occurred_on=m.occurred_on, payer_member_id=m.payer_member_id,
        total_amount=m.total_amount, note=m.note, raw_input=m.raw_input, dish=m.dish, place_id=m.place_id,
        initiator=m.initiator, guests=list(m.guests or []), source=m.source, logged_by=m.logged_by,
        voided=bool(m.voided), voided_by=m.voided_by, voided_at=_naive(m.voided_at),
        created_at=_naive(m.created_at),
        shares=tuple(ShareRecord(m.id, s.member_id, s.share_amount) for s in sorted(m.shares, key=lambda s: s.id)))
        for m in session.scalars(select(Meal).where(Meal.room_id == room_id).order_by(Meal.id))]
    payments = [PaymentRecord(
        id=p.id, room_id=p.room_id, from_member_id=p.from_member_id, to_member_id=p.to_member_id,
        amount=p.amount, occurred_on=p.occurred_on, meal_id=p.meal_id, ref_kind=p.ref_kind, note=p.note,
        source=p.source, logged_by=p.logged_by, voided=bool(p.voided), voided_by=p.voided_by,
        voided_at=_naive(p.voided_at), created_at=_naive(p.created_at))
        for p in session.scalars(select(Payment).where(Payment.room_id == room_id).order_by(Payment.id))]
    settlements = [SettlementRecord(
        id=st.id, room_id=st.room_id, period_from=st.period_from, period_to=st.period_to,
        created_at=_naive(st.created_at), requested_by=st.requested_by, transfers=list(st.transfers or []))
        for st in session.scalars(select(Settlement).where(Settlement.room_id == room_id).order_by(Settlement.id))]
    return meals, payments, settlements


def _naive(value):
    """A datetime as the table returns it from a fresh read: local wall time, no tz."""
    return value.replace(tzinfo=None) if value is not None else None


def differences(session: Session, room_id: int) -> list[str]:
    """Every way the journal's view of ``room_id`` differs from its tables, field by
    field — empty when they agree."""
    return _compare(session, room_id)[0]


_ROWS = {"meal": (Meal, journal._meal_inserted), "payment": (Payment, journal._payment_inserted),
         "settlement": (Settlement, journal._settlement_inserted)}


def reconcile(session: Session, room_id: int) -> int:
    """Append the tables' current state of every row the journal lacks or holds
    differently; returns how many. The way forward again after the previous commit —
    which writes the tables only — served for a while (review B#1): a full-state event
    replaces whatever the journal held for that id. A row only the journal holds is
    left for the parity check to refuse: the tables never lose a row."""
    _, stale = _compare(session, room_id)
    for label, rid in sorted(stale):
        model, build = _ROWS[label]
        ev = build(session.get(model, rid))[0]
        ev.pop("_order", None)
        journal.append_raw(session, "ledger", room_id, {**ev, "reconciled": True})
    return len(stale)


def _compare(session: Session, room_id: int) -> tuple[list[str], set[tuple[str, int]]]:
    """``(differences, stale)``: the messages, and the ``(kind, id)`` of every row the
    tables hold that the journal lacks or holds differently."""
    t_meals, t_payments, t_settlements = table_records(session, room_id)
    view = LedgerView(session, room_id)
    out, stale = [], set()
    for label, table, journal_side in (("meal", t_meals, view.meals(voided=None)),
                                       ("payment", t_payments, view.payments(voided=None)),
                                       ("settlement", t_settlements, view.settlements())):
        t_by, j_by = {r.id: r for r in table}, {r.id: r for r in journal_side}
        for rid in sorted(set(t_by) | set(j_by)):
            a, b = t_by.get(rid), j_by.get(rid)
            if a is None or b is None:
                out.append(f"room {room_id} {label} #{rid}: only in the {'journal' if a is None else 'tables'}")
                if b is None:
                    stale.add((label, rid))
                continue
            for f in fields(a):
                if getattr(a, f.name) != getattr(b, f.name):
                    out.append(f"room {room_id} {label} #{rid}.{f.name}: tables {getattr(a, f.name)!r} "
                               f"!= journal {getattr(b, f.name)!r}")
                    stale.add((label, rid))
    return out, stale
