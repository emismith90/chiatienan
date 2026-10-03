"""Append-only ledger operations over the SQLAlchemy models.

Every function takes an open :class:`~sqlalchemy.orm.Session`; the caller's
session scope owns the transaction. All arithmetic is delegated to
:mod:`ledger_core.money` — this module only reads/writes rows and derives
balances. Dates come from :mod:`ledger_core.clock`; member existence from the
host's :mod:`ledger_core.members` directory (review F3).
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger_core import clock
from ledger_core import members as _members
from ledger_core.models import Meal, MealShare, Payment, Settlement
from ledger_core.view import LedgerView
from ledger_core.money import (
    Transfer,
    apply_payments_fifo,
    build_debt_edges,
    net_transfers,
    split_with_guests,
)

if TYPE_CHECKING:
    from ledger_core.money import DebtEdge


class LedgerError(ValueError):
    """A ledger write was rejected (bad payer/participant/void target)."""


def record_meal(
    session: Session,
    *,
    room_id: int,
    payer_member_id: int,
    participants: list[int],
    total_amount: int,
    adjustments: dict[int, int] | None = None,
    guests: list[str] | None = None,
    dish: str | None = None,
    place_id: int | None = None,
    initiator: str | None = None,
    occurred_on: date | None = None,
    note: str | None = None,
    raw_input: str | None = None,
    source: str = "web",
    logged_by: str | None = None,
) -> dict:
    """Validate, split, and write ``meals`` + ``meal_shares`` in one transaction.

    ``total_amount`` is the bill the group saw; ``guests`` are occasional
    non-members who pay their share in cash (they shrink the per-head but are
    never billed). The persisted ``Meal.total_amount`` is the **tracked** member
    total (bill − guest total), so balances/settlement stay correct.
    """
    guests = list(guests or [])
    directory = _members.directory()
    if directory.get(session, room_id, payer_member_id) is None:
        raise LedgerError(f"Payer (id={payer_member_id}) does not exist.")

    known = directory.ids_in_space(session, room_id, list(participants))
    missing = [p for p in participants if p not in known]
    if missing:
        raise LedgerError(f"Participants do not exist: {missing}.")

    split = split_with_guests(
        total_amount, participants, len(guests), adjustments, payer_id=payer_member_id
    )
    shares = split["shares"]
    tracked_total = split["tracked_total"]

    meal = Meal(
        room_id=room_id,
        occurred_on=occurred_on or clock.today(),
        payer_member_id=payer_member_id,
        total_amount=tracked_total,
        note=note,
        raw_input=raw_input,
        dish=dish,
        place_id=place_id,
        initiator=initiator,
        guests=guests,
        source=source,
        logged_by=logged_by,
    )
    meal.shares = [MealShare(member_id=mid, share_amount=amt) for mid, amt in shares.items()]
    session.add(meal)
    session.flush()

    return {
        "meal_id": meal.id,
        "occurred_on": meal.occurred_on.isoformat(),
        "payer_member_id": payer_member_id,
        "place_id": place_id,
        "bill_total": total_amount,
        "tracked_total": tracked_total,
        "total_amount": tracked_total,
        "guests": guests,
        "shares": dict(shares),
    }


def record_payment(
    session: Session,
    *,
    room_id: int,
    from_member_id: int,
    to_member_id: int,
    amount: int,
    occurred_on: date | None = None,
    note: str | None = None,
    source: str = "web",
    logged_by: str | None = None,
    meal_id: int | None = None,
) -> dict:
    """Record a cash payment from one member to another (adjusts balances)."""
    if amount <= 0:
        raise LedgerError("Payment amount must be greater than 0.")
    if from_member_id == to_member_id:
        raise LedgerError("A payment must be between two different members.")
    found = _members.directory().ids_in_space(session, room_id, [from_member_id, to_member_id])
    for mid in (from_member_id, to_member_id):
        if mid not in found:
            raise LedgerError(f"Member (id={mid}) does not exist.")

    pay = Payment(
        room_id=room_id,
        from_member_id=from_member_id,
        to_member_id=to_member_id,
        amount=amount,
        occurred_on=occurred_on or clock.today(),
        note=note,
        source=source,
        logged_by=logged_by,
        meal_id=meal_id,
    )
    session.add(pay)
    session.flush()
    return {
        "payment_id": pay.id,
        "from_member_id": from_member_id,
        "to_member_id": to_member_id,
        "amount": amount,
        "occurred_on": pay.occurred_on.isoformat(),
    }


def repoint_meal_payments(session: Session, *, room_id: int, old_meal_id: int,
                          new_meal_id: int | None) -> int:
    """Move payments stamped with ``old_meal_id`` onto ``new_meal_id`` (or None).

    The ⑦ quick-pay button records a payment against a specific meal. Voiding
    that meal, or editing it (which voids and re-records under a NEW id), used to
    leave the payment pointing at a meal that no longer exists — real money,
    still owed to the same person, that one view counted and another did not.
    Passing ``None`` un-targets them so they fall back to the pair pool.
    """
    payments = session.scalars(
        select(Payment).where(Payment.room_id == room_id, Payment.meal_id == old_meal_id)
    ).all()
    for pay in payments:
        pay.meal_id = new_meal_id
    if payments:
        session.flush()
    return len(payments)


def void_meal(session: Session, meal_id: int, *, room_id: int, by: str | None = None,
              untarget_payments: bool = True) -> dict:
    """Soft-delete a meal for a correction (design 6.1: void, then re-record).

    ``untarget_payments=True`` (the default, and what the standalone void tool
    uses) un-targets the meal's payments immediately: there is no new meal for
    them to follow, so the pair pool is the only place left for them to keep
    counting. ``recommit_draft`` (an edit: void, then re-record under a NEW meal
    id) passes ``untarget_payments=False`` and repoints those payments itself
    once the new meal exists, because doing it here — before the new meal is
    known — is what let a quick-paid payment go untargeted, fall into the pair
    pool, and silently cancel an unrelated debt when the edit changed the payer
    (see the comment in ``recommit_draft``).
    """
    meal = session.get(Meal, meal_id)
    if meal is None or meal.room_id != room_id:
        raise LedgerError(f"Meal #{meal_id} not found.")
    if meal.voided:
        return {"meal_id": meal_id, "already_voided": True}
    meal.voided = True
    meal.voided_by = by
    meal.voided_at = clock.now()
    untargeted = 0
    if untarget_payments:
        # The meal is gone but the money was still handed over: un-target its
        # payments so they keep counting against what this pair owes.
        untargeted = repoint_meal_payments(session, room_id=room_id, old_meal_id=meal_id,
                                          new_meal_id=None)
    session.flush()
    return {"meal_id": meal_id, "voided": True, "payments_untargeted": untargeted}


def void_payment(session: Session, payment_id: int, *, room_id: int, by: str | None = None) -> dict:
    """Soft-delete a payment for a correction (design 6.1: void, then re-record).

    A payment's docstring calls it append-only — corrections are meant to be a
    void plus a new payment, never an edit or a delete — but until now nothing
    actually set ``voided``: every ledger query that filters on it
    (``debt_breakdown``, ``period_timeline``, ``outstanding_pairs``,
    ``statement_for``, …) had no way to ever be shown a voided payment, because
    the button to make one did not exist. The row survives a void because it is
    still evidence cash changed hands on a given day; deleting it would erase
    that history along with whatever mistake it is correcting, and would leave
    no trace of who reversed it or when.

    This closes the gap that let a quick-paid, untargeted payment (``meal_id``
    None) sit in the pair pool after the meal it was for had its payer changed
    out from under it, and silently cancel an unrelated debt a week later — with
    no way back short of a hand-written UPDATE on the production file.
    """
    pay = session.get(Payment, payment_id)
    if pay is None or pay.room_id != room_id:
        raise LedgerError(f"Payment #{payment_id} not found.")
    if pay.voided:
        return {"payment_id": payment_id, "already_voided": True}
    pay.voided = True
    pay.voided_by = by
    pay.voided_at = clock.now()
    session.flush()
    return {"payment_id": payment_id, "voided": True}


def period_balances(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> dict[int, dict[str, int]]:
    """Per-member ``paid`` / ``consumed`` / ``balance`` over an inclusive window.

    **Internal ledger math — not for display.** Nothing user-facing reports a
    net balance any more (see :func:`statement_for`); this survives because it
    is how the tests state the invariants a split has to satisfy (shares sum to
    the bill, balances sum to zero, a void leaves nobody owing). Report
    :func:`statement_for` or :func:`outstanding_pairs` instead.

    Excludes voided meals. ``from_date=None`` means "from the beginning of the
    ledger". Only members with any activity in the window appear. Scoped to
    ``room_id`` — other rooms' meals never contribute.

    ``paid`` and ``consumed`` are what they say: cash fronted and food eaten, in
    the window. ``balance`` is the **debt** position and comes from
    :func:`debt_breakdown`, not from ``paid − consumed ± payments``.

    That subtraction is what made the numbers feel unstable. It folded in every
    payment dated inside the window regardless of which meal it settled, so on
    2026-07-27 alone it reported Giang +107,000 / Linh −107,000 — a debt nobody
    held, from a payment for meals on the 23rd and 24th — while ``settle_period``
    for that same day correctly listed no transfer between them. Deriving the
    balance from the same edges the transfers and statements use means the three
    can no longer contradict each other.
    """
    view = LedgerView(session, room_id)
    meals = view.meals(from_date=from_date, to_date=to_date)
    out: dict[int, dict[str, int]] = {}

    # paid: sum of meals where member is payer
    for m in meals:
        out.setdefault(m.payer_member_id, {"paid": 0, "consumed": 0, "balance": 0})
        out[m.payer_member_id]["paid"] += m.total_amount

    # consumed: sum of shares on non-voided meals in the window
    for m in meals:
        for sh in m.shares:
            out.setdefault(sh.member_id, {"paid": 0, "consumed": 0, "balance": 0})
            out[sh.member_id]["consumed"] += sh.share_amount

    # Anyone who only moved cash in this window still belongs in the list, even
    # though their debt position may be zero — dropping them would hide a
    # payment from the period view entirely.
    for from_id, to_id in ((p.from_member_id, p.to_member_id)
                           for p in view.payments(from_date=from_date, to_date=to_date)):
        out.setdefault(from_id, {"paid": 0, "consumed": 0, "balance": 0})
        out.setdefault(to_id, {"paid": 0, "consumed": 0, "balance": 0})

    for row in out.values():
        row["balance"] = 0
    for e in debt_breakdown(session, room_id, from_date, to_date):
        if e.outstanding <= 0:
            continue
        out.setdefault(e.creditor, {"paid": 0, "consumed": 0, "balance": 0})
        out.setdefault(e.debtor, {"paid": 0, "consumed": 0, "balance": 0})
        out[e.creditor]["balance"] += e.outstanding
        out[e.debtor]["balance"] -= e.outstanding

    return out


def period_transfer_inputs(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> tuple[list[dict], list[dict]]:
    """Per-meal shares + ad-hoc payments in the window, shaped for
    :func:`ledger_core.money.per_payer_transfers`.

    Returns ``(meals, payments)`` where each meal is
    ``{"payer_id", "shares": {member_id: amount}}`` and each payment is
    ``{"from", "to", "amount"}``. Excludes voided meals and voided payments;
    ``from_date=None`` means "from the beginning of the ledger". Same window
    semantics as :func:`period_balances`, so the two always agree.
    """
    view = LedgerView(session, room_id)
    meals = [{"payer_id": m.payer_member_id, "shares": {sh.member_id: sh.share_amount for sh in m.shares}}
             for m in view.meals(from_date=from_date, to_date=to_date)]
    payments = [{"from": p.from_member_id, "to": p.to_member_id, "amount": p.amount}
                for p in view.payments(from_date=from_date, to_date=to_date)]
    return meals, payments


#: Where the debt edges come from (design §4.2 "balance contributions"; plan Task
#: 3.4). Each source is ``(session, space_id) -> list[DebtEdge]`` returning **every**
#: gross edge of that business, unwindowed and with ``paid=0`` — the core applies
#: payments FIFO over the sum of all sources and windows afterwards (review F4), so
#: no pack can re-window edges before attribution. A host registers its packs'
#: ``contributions`` through :func:`set_edge_sources` (``ledger_core.configure``);
#: with none registered the ledger reads its own meals, as it always has.
EdgeSource = Callable[[Session, int], list["DebtEdge"]]
_edge_sources: list[EdgeSource] | None = None


def set_edge_sources(sources: list[EdgeSource] | None) -> None:
    global _edge_sources
    _edge_sources = list(sources) if sources is not None else None


def meal_edges(session: Session, room_id: int) -> list["DebtEdge"]:
    """The meals' gross edges — one per (participant ≠ payer, non-voided meal), the
    whole ledger of ``room_id``, ``paid=0``. The lunch business's contribution."""
    return build_debt_edges([
        {"meal_id": m.id, "payer_id": m.payer_member_id, "dish": m.dish, "occurred_on": m.occurred_on,
         "shares": {sh.member_id: sh.share_amount for sh in m.shares}}
        for m in LedgerView(session, room_id).meals()])


def _all_edges(session: Session, room_id: int) -> list["DebtEdge"]:
    sources = _edge_sources if _edge_sources is not None else [meal_edges]
    return [e for source in sources for e in source(session, room_id)]


def debt_breakdown(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> list["DebtEdge"]:
    """Gross per-(debtor, creditor, meal) edges with FIFO-attributed payments.

    Excludes voided meals and voided payments. Does NOT net opposing debts —
    that is the whole point (a person's real debt to a creditor, per meal).

    **The window filters meals, not payments.** Attribution runs over the whole
    ledger and only the resulting edges are filtered by ``occurred_on``, because
    a debt is not outstanding again just because it was repaid after the window
    closed. Windowing the payments too — which this used to do, in step with
    :func:`period_balances` — meant that asking "chốt tuần trước" ("close last week") on a Monday
    reported the 107,000đ Giang had paid that same morning as still owing, and
    printed a live VietQR for it. In a room that habitually pays the next day,
    every week-scoped question had that property.

    The edges themselves come from every registered source (:data:`_edge_sources`
    — the enabled packs' ``contributions``), the meals by default.
    """
    payments = [
        {"from": p.from_member_id, "to": p.to_member_id, "amount": p.amount, "meal_id": p.meal_id,
         "ref_kind": p.ref_kind}
        for p in LedgerView(session, room_id).payments()
    ]
    edges = apply_payments_fifo(_all_edges(session, room_id), payments)
    return [e for e in edges
            if e.occurred_on <= to_date and (from_date is None or e.occurred_on >= from_date)]


def period_transfers(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> list[Transfer]:
    """Who pays whom for the meals in the window — the settlement's source.

    Derived from :func:`debt_breakdown`, so the transfer amounts, the per-meal
    QR notes and :func:`statement_for` are three views of one computation
    instead of three computations that have to be kept in agreement.
    """
    return net_transfers(debt_breakdown(session, room_id, from_date, to_date))


def statement_for(
    session: Session, room_id: int, member_id: int, from_date: date | None, to_date: date
) -> dict:
    """The caller's own owe/owed edges (outstanding > 0). Ids only.

    Deliberately returns no net figure. "Ròng: -54.500đ" ("Net: -54,500đ") answered a question
    nobody asked — the group's questions are "tôi nợ ai" ("who do I owe") and "ai nợ tôi" ("who owes me"), and a
    single signed scalar answers neither while quietly implying the debts had
    been offset against each other. They are not: each edge is owed to a
    specific person for a specific meal, and that is all any surface reports.
    """
    edges = debt_breakdown(session, room_id, from_date, to_date)
    owe = [{"other_id": e.creditor, "meal_id": e.meal_id, "dish": e.dish,
            "occurred_on": e.occurred_on.isoformat(), "amount": e.outstanding, "status": e.status}
           for e in edges if e.debtor == member_id and e.outstanding > 0]
    owed = [{"other_id": e.debtor, "meal_id": e.meal_id, "dish": e.dish,
             "occurred_on": e.occurred_on.isoformat(), "amount": e.outstanding, "status": e.status}
            for e in edges if e.creditor == member_id and e.outstanding > 0]
    return {"owe": owe, "owed": owed}


def outstanding_pairs(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> list[dict]:
    """Every open debt in the room as ``debtor owes creditor`` rows.

    The group-wide counterpart to :func:`statement_for`, and what replaced the
    per-person balance bars: a direction and an amount per pair, summed over the
    pair's meals. Opposing directions are **not** netted here — if A owes B and
    B owes A, both rows appear, because that is what the two of them each have
    to settle. Netting is :func:`period_transfers`' job, for QR codes only.

    Sorted by amount descending, then by ids, so the ordering is stable.
    """
    totals: dict[tuple[int, int], int] = {}
    for e in debt_breakdown(session, room_id, from_date, to_date):
        if e.outstanding > 0:
            totals[(e.debtor, e.creditor)] = totals.get((e.debtor, e.creditor), 0) + e.outstanding
    return [{"debtor_id": d, "creditor_id": c, "amount": amt}
            for (d, c), amt in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))]


#: Where a period's timeline events come from (Phase 6 review F2): each source is
#: ``(session, space_id, from_date, to_date) -> list[dict]`` of ``{"kind", "occurred_on",
#: "created_at", …}`` rows. The ledger adds its payments itself. With none registered
#: the timeline is the meals', as it always was.
TimelineSource = Callable[[Session, int, "date | None", date], list[dict]]
_timeline_sources: list[TimelineSource] | None = None


def set_timeline_sources(sources: list[TimelineSource] | None) -> None:
    global _timeline_sources
    _timeline_sources = list(sources) if sources is not None else None


def meal_timeline(session: Session, room_id: int, from_date: date | None, to_date: date) -> list[dict]:
    """The meals in the window as timeline events — the lunch business's source."""
    return [{"kind": "meal", "meal_id": m.id, "payer_id": m.payer_member_id, "dish": m.dish,
             "occurred_on": m.occurred_on.isoformat(), "total": m.total_amount,
             "participant_ids": [sh.member_id for sh in m.shares],
             "created_at": m.created_at.isoformat() if m.created_at else ""}
            for m in LedgerView(session, room_id).meals(from_date=from_date, to_date=to_date)]


def period_timeline(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> list[dict]:
    """Every registered source's events + payments in the window, as one list ordered
    by (occurred_on, created_at)."""
    sources = _timeline_sources if _timeline_sources is not None else [meal_timeline]
    events: list[dict] = [e for source in sources for e in source(session, room_id, from_date, to_date)]
    for p in LedgerView(session, room_id).payments(from_date=from_date, to_date=to_date):
        events.append({"kind": "payment", "payment_id": p.id, "from_id": p.from_member_id,
                       "to_id": p.to_member_id, "amount": p.amount, "occurred_on": p.occurred_on.isoformat(),
                       "created_at": p.created_at.isoformat() if p.created_at else ""})
    events.sort(key=lambda e: (e["occurred_on"], e["created_at"]))
    return events


def period_meal_details(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> list[dict]:
    """Per-meal metadata in the window, for building settlement QR notes.

    Returns ``[{"payer_id", "occurred_on", "dish", "shares": {member_id: amount}}]``
    — the date/dish that :func:`period_transfer_inputs` drops, plus the shares so a
    transfer can be attributed to the meals a debtor took part in. Same window and
    void semantics as :func:`period_transfer_inputs`, so the two agree.
    """
    return [{"payer_id": m.payer_member_id, "occurred_on": m.occurred_on, "dish": m.dish,
             "shares": {sh.member_id: sh.share_amount for sh in m.shares}}
            for m in LedgerView(session, room_id).meals(from_date=from_date, to_date=to_date)]


def last_settlement(session: Session, room_id: int) -> "SettlementRecord | None":
    """The latest settlement by ``period_to``, then id — from the journal."""
    found = sorted(LedgerView(session, room_id).settlements(), key=lambda st: (st.period_to, st.id))
    return found[-1] if found else None


def record_settlement(
    session: Session,
    *,
    room_id: int,
    period_from: date | None,
    period_to: date,
    requested_by: str | None,
    transfers: list[dict],
) -> Settlement:
    """Append a committed settle event (the only thing that closes a period)."""
    row = Settlement(
        room_id=room_id,
        period_from=period_from,
        period_to=period_to,
        requested_by=requested_by,
        transfers=transfers,
    )
    session.add(row)
    session.flush()
    return row
