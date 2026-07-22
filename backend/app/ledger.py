"""Append-only ledger operations over the SQLAlchemy models.

Every function takes an open :class:`~sqlalchemy.orm.Session`; the caller's
``Database.session()`` scope owns the transaction. All arithmetic is delegated to
:mod:`app.money` — this module only reads/writes rows and derives balances.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import now_ict, today_ict
from app.models import Meal, MealShare, Member, Payment, Settlement
from app.money import split_with_guests


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
    payer = session.get(Member, payer_member_id)
    if payer is None or payer.room_id != room_id:
        raise LedgerError(f"Payer (id={payer_member_id}) does not exist.")

    known = {
        m.id
        for m in session.scalars(
            select(Member).where(Member.id.in_(participants), Member.room_id == room_id)
        )
    }
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
        occurred_on=occurred_on or today_ict(),
        payer_member_id=payer_member_id,
        total_amount=tracked_total,
        note=note,
        raw_input=raw_input,
        dish=dish,
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
) -> dict:
    """Record a cash payment from one member to another (adjusts balances)."""
    if amount <= 0:
        raise LedgerError("Payment amount must be greater than 0.")
    if from_member_id == to_member_id:
        raise LedgerError("A payment must be between two different members.")
    found = {
        m.id
        for m in session.scalars(
            select(Member).where(
                Member.id.in_([from_member_id, to_member_id]), Member.room_id == room_id
            )
        )
    }
    for mid in (from_member_id, to_member_id):
        if mid not in found:
            raise LedgerError(f"Member (id={mid}) does not exist.")

    pay = Payment(
        room_id=room_id,
        from_member_id=from_member_id,
        to_member_id=to_member_id,
        amount=amount,
        occurred_on=occurred_on or today_ict(),
        note=note,
        source=source,
        logged_by=logged_by,
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


def void_meal(session: Session, meal_id: int, *, room_id: int, by: str | None = None) -> dict:
    """Soft-delete a meal for a correction (design 6.1: void, then re-record)."""
    meal = session.get(Meal, meal_id)
    if meal is None or meal.room_id != room_id:
        raise LedgerError(f"Meal #{meal_id} not found.")
    if meal.voided:
        return {"meal_id": meal_id, "already_voided": True}
    meal.voided = True
    meal.voided_by = by
    meal.voided_at = now_ict()
    session.flush()
    return {"meal_id": meal_id, "voided": True}


def period_balances(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> dict[int, dict[str, int]]:
    """Per-member ``paid`` / ``consumed`` / ``balance`` over an inclusive window.

    Excludes voided meals. ``from_date=None`` means "from the beginning of the
    ledger". Only members with any activity in the window appear. Scoped to
    ``room_id`` — other rooms' meals never contribute. Also folds in ad-hoc
    payments in the window: the payer's balance gets ``+amount`` and the
    payee's gets ``-amount`` (voided payments excluded), so a member who only
    made/received a payment — with no meals of their own — still appears.
    """
    def _in_window(col):
        conds = [Meal.room_id == room_id, Meal.voided.is_(False), col <= to_date]
        if from_date is not None:
            conds.append(col >= from_date)
        return conds

    out: dict[int, dict[str, int]] = {}

    # paid: sum of meals where member is payer
    paid_rows = session.execute(
        select(Meal.payer_member_id, Meal.total_amount).where(*_in_window(Meal.occurred_on))
    ).all()
    for payer_id, total in paid_rows:
        out.setdefault(payer_id, {"paid": 0, "consumed": 0, "balance": 0})
        out[payer_id]["paid"] += total

    # consumed: sum of shares on non-voided meals in the window
    consumed_rows = session.execute(
        select(MealShare.member_id, MealShare.share_amount)
        .join(Meal, MealShare.meal_id == Meal.id)
        .where(*_in_window(Meal.occurred_on))
    ).all()
    for member_id, amt in consumed_rows:
        out.setdefault(member_id, {"paid": 0, "consumed": 0, "balance": 0})
        out[member_id]["consumed"] += amt

    for row in out.values():
        row["balance"] = row["paid"] - row["consumed"]

    # Fold ad-hoc payments: a payment from X to Y increases X's balance (their
    # debt shrinks) and decreases Y's. Done after the paid-consumed loop so it
    # is not overwritten. Voided payments are excluded.
    pay_conds = [Payment.room_id == room_id, Payment.voided.is_(False), Payment.occurred_on <= to_date]
    if from_date is not None:
        pay_conds.append(Payment.occurred_on >= from_date)
    pay_rows = session.execute(
        select(Payment.from_member_id, Payment.to_member_id, Payment.amount).where(*pay_conds)
    ).all()
    for from_id, to_id, amt in pay_rows:
        out.setdefault(from_id, {"paid": 0, "consumed": 0, "balance": 0})
        out.setdefault(to_id, {"paid": 0, "consumed": 0, "balance": 0})
        out[from_id]["balance"] += amt
        out[to_id]["balance"] -= amt

    return out


def period_transfer_inputs(
    session: Session, room_id: int, from_date: date | None, to_date: date
) -> tuple[list[dict], list[dict]]:
    """Per-meal shares + ad-hoc payments in the window, shaped for
    :func:`app.money.per_payer_transfers`.

    Returns ``(meals, payments)`` where each meal is
    ``{"payer_id", "shares": {member_id: amount}}`` and each payment is
    ``{"from", "to", "amount"}``. Excludes voided meals and voided payments;
    ``from_date=None`` means "from the beginning of the ledger". Same window
    semantics as :func:`period_balances`, so the two always agree.
    """
    meal_conds = [Meal.room_id == room_id, Meal.voided.is_(False), Meal.occurred_on <= to_date]
    if from_date is not None:
        meal_conds.append(Meal.occurred_on >= from_date)

    meal_rows = session.execute(
        select(Meal.id, Meal.payer_member_id).where(*meal_conds)
    ).all()
    by_id = {mid: {"payer_id": payer, "shares": {}} for mid, payer in meal_rows}

    if by_id:
        share_rows = session.execute(
            select(MealShare.meal_id, MealShare.member_id, MealShare.share_amount)
            .where(MealShare.meal_id.in_(by_id.keys()))
        ).all()
        for meal_id, member_id, amt in share_rows:
            by_id[meal_id]["shares"][member_id] = amt

    pay_conds = [Payment.room_id == room_id, Payment.voided.is_(False), Payment.occurred_on <= to_date]
    if from_date is not None:
        pay_conds.append(Payment.occurred_on >= from_date)
    payments = [
        {"from": frm, "to": to, "amount": amt}
        for frm, to, amt in session.execute(
            select(Payment.from_member_id, Payment.to_member_id, Payment.amount).where(*pay_conds)
        ).all()
    ]

    return list(by_id.values()), payments


def last_settlement(session: Session, room_id: int) -> Settlement | None:
    return session.scalars(
        select(Settlement)
        .where(Settlement.room_id == room_id)
        .order_by(Settlement.period_to.desc(), Settlement.id.desc())
        .limit(1)
    ).first()


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
