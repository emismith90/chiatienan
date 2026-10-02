"""A room's ledger, read from its journal (plan 2026-10-02, release B, B4).

:class:`LedgerView` folds the room's ``ledger`` events, oldest first, into frozen
records with the attribute names of the ORM rows (``Meal`` with ``.shares``,
``Payment``, ``Settlement``), so the money code reads them exactly as it read rows.
Records are frozen: a write through a record raises instead of silently doing nothing —
writes go through :mod:`ledger_core.ledger`, which the journal mirrors.

Order: meals and payments by id, shares in the order they were recorded — the order the
tables give back, which FIFO and every statement rely on. Types come back as the tables
return them: dates, naive local datetimes, integer ids.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ledger_core.journal import load_date, load_datetime


@dataclass(frozen=True)
class ShareRecord:
    meal_id: int
    member_id: int
    share_amount: int


@dataclass(frozen=True)
class MealRecord:
    id: int
    room_id: int
    occurred_on: date
    payer_member_id: int
    total_amount: int
    note: str | None = None
    raw_input: str | None = None
    dish: str | None = None
    place_id: int | None = None
    initiator: str | None = None
    guests: list = field(default_factory=list)
    source: str = "web"
    logged_by: str | None = None
    voided: bool = False
    voided_by: str | None = None
    voided_at: datetime | None = None
    created_at: datetime | None = None
    shares: tuple[ShareRecord, ...] = ()


@dataclass(frozen=True)
class PaymentRecord:
    id: int
    room_id: int
    from_member_id: int
    to_member_id: int
    amount: int
    occurred_on: date
    meal_id: int | None = None
    ref_kind: str = "meal"
    note: str | None = None
    source: str = "web"
    logged_by: str | None = None
    voided: bool = False
    voided_by: str | None = None
    voided_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class SettlementRecord:
    id: int
    room_id: int
    period_from: date | None
    period_to: date
    created_at: datetime | None = None
    requested_by: str | None = None
    transfers: list = field(default_factory=list)


def _meal(ev: dict) -> MealRecord:
    return MealRecord(
        id=ev["id"], room_id=ev["room_id"], occurred_on=load_date(ev["occurred_on"]),
        payer_member_id=ev["payer_member_id"], total_amount=ev["total_amount"], note=ev.get("note"),
        raw_input=ev.get("raw_input"), dish=ev.get("dish"), place_id=ev.get("place_id"),
        initiator=ev.get("initiator"), guests=list(ev.get("guests") or []), source=ev.get("source") or "web",
        logged_by=ev.get("logged_by"), voided=bool(ev.get("voided")), voided_by=ev.get("voided_by"),
        voided_at=load_datetime(ev.get("voided_at")), created_at=load_datetime(ev.get("created_at")),
        shares=tuple(ShareRecord(ev["id"], s["member_id"], s["share_amount"]) for s in ev.get("shares") or []))


def _payment(ev: dict) -> PaymentRecord:
    return PaymentRecord(
        id=ev["id"], room_id=ev["room_id"], from_member_id=ev["from_member_id"],
        to_member_id=ev["to_member_id"], amount=ev["amount"], occurred_on=load_date(ev["occurred_on"]),
        meal_id=ev.get("meal_id"), ref_kind=ev.get("ref_kind") or "meal", note=ev.get("note"),
        source=ev.get("source") or "web", logged_by=ev.get("logged_by"), voided=bool(ev.get("voided")),
        voided_by=ev.get("voided_by"), voided_at=load_datetime(ev.get("voided_at")),
        created_at=load_datetime(ev.get("created_at")))


def _settlement(ev: dict) -> SettlementRecord:
    return SettlementRecord(
        id=ev["id"], room_id=ev["room_id"], period_from=load_date(ev.get("period_from")),
        period_to=load_date(ev["period_to"]), created_at=load_datetime(ev.get("created_at")),
        requested_by=ev.get("requested_by"), transfers=list(ev.get("transfers") or []))


def fold(events: list[dict]) -> tuple[dict[int, MealRecord], dict[int, PaymentRecord], dict[int, SettlementRecord]]:
    """Apply events in order. An event about a record the journal does not hold is a
    corrupt journal, not something to skip: it raises."""
    meals: dict[int, MealRecord] = {}
    payments: dict[int, PaymentRecord] = {}
    settlements: dict[int, SettlementRecord] = {}
    for ev in events:
        try:
            _apply(ev, meals, payments, settlements)
        except KeyError as exc:
            raise ValueError(f"ledger event {ev.get('event')!r} is about {exc} before the journal "
                             "records it: the journal does not start at the beginning") from None
    return meals, payments, settlements


def _apply(ev: dict, meals: dict, payments: dict, settlements: dict) -> None:
    kind = ev["event"]
    if kind == "meal":
        meals[ev["id"]] = _meal(ev)
    elif kind == "meal_void":
        meals[ev["meal_id"]] = replace(meals[ev["meal_id"]], voided=bool(ev["voided"]),
                                       voided_by=ev.get("voided_by"),
                                       voided_at=load_datetime(ev.get("voided_at")))
    elif kind == "meal_place":
        meals[ev["meal_id"]] = replace(meals[ev["meal_id"]], place_id=ev.get("place_id"))
    elif kind == "payment":
        payments[ev["id"]] = _payment(ev)
    elif kind == "payment_void":
        payments[ev["payment_id"]] = replace(payments[ev["payment_id"]], voided=bool(ev["voided"]),
                                             voided_by=ev.get("voided_by"),
                                             voided_at=load_datetime(ev.get("voided_at")))
    elif kind == "payment_retarget":
        payments[ev["payment_id"]] = replace(payments[ev["payment_id"]], meal_id=ev.get("meal_id"))
    elif kind == "settlement":
        settlements[ev["id"]] = _settlement(ev)
    else:
        raise ValueError(f"unknown ledger event {kind!r}")


def journal_events(session: Session, slug: str, room_id: Any) -> list[dict]:
    """Every event of one room's journal, in order — uncapped (review R6)."""
    from kernos.data.system import SYSTEM_BUSINESS
    from sqlalchemy import select

    from kernos.content import models as km

    cid = session.scalar(select(km.Collection.id).join(km.Business, km.Business.id == km.Collection.business_id)
                         .where(km.Business.slug == SYSTEM_BUSINESS, km.Collection.slug == slug))
    if cid is None:
        raise RuntimeError(f"the {slug!r} journal is missing: was the schema bound (ledger_core.bind)?")
    return list(session.scalars(select(km.Document.data).where(
        km.Document.collection_id == cid, km.Document.space_id == str(room_id)).order_by(km.Document.doc_id)))


class LedgerView:
    """One room's ledger as of the session's view of the journal."""

    def __init__(self, session: Session, room_id: int) -> None:
        self.room_id = room_id
        meals, payments, settlements = fold(journal_events(session, "ledger", room_id))
        self._meals, self._payments, self._settlements = meals, payments, settlements

    def meals(self, *, voided: bool | None = False, from_date: date | None = None,
              to_date: date | None = None) -> list[MealRecord]:
        """Meals by id; ``voided=None`` for all. A window filters ``occurred_on``."""
        out = []
        for m in sorted(self._meals.values(), key=lambda m: m.id):
            if voided is not None and m.voided != voided:
                continue
            if to_date is not None and m.occurred_on > to_date:
                continue
            if from_date is not None and m.occurred_on < from_date:
                continue
            out.append(m)
        return out

    def payments(self, *, voided: bool | None = False, from_date: date | None = None,
                 to_date: date | None = None) -> list[PaymentRecord]:
        out = []
        for p in sorted(self._payments.values(), key=lambda p: p.id):
            if voided is not None and p.voided != voided:
                continue
            if to_date is not None and p.occurred_on > to_date:
                continue
            if from_date is not None and p.occurred_on < from_date:
                continue
            out.append(p)
        return out

    def meal(self, meal_id: int) -> MealRecord | None:
        return self._meals.get(meal_id)

    def payment(self, payment_id: int) -> PaymentRecord | None:
        return self._payments.get(payment_id)

    def settlements(self) -> list[SettlementRecord]:
        return sorted(self._settlements.values(), key=lambda st: st.id)
