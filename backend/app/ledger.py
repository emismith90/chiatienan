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
from app.models import Meal, MealShare, Member, Settlement
from app.money import split_shares


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
    occurred_on: date | None = None,
    note: str | None = None,
    raw_input: str | None = None,
    source: str = "web",
    logged_by: str | None = None,
) -> dict:
    """Validate, split, and write ``meals`` + ``meal_shares`` in one transaction.

    The payer need not be a participant (they paid but didn't eat). Returns a
    breakdown dict with the persisted ``meal_id`` and per-member shares — the
    single source of truth the reply is rendered from (numbers never go back
    through the LLM).
    """
    payer = session.get(Member, payer_member_id)
    if payer is None or payer.room_id != room_id:
        raise LedgerError(f"Người trả tiền (id={payer_member_id}) không tồn tại.")

    known = {
        m.id
        for m in session.scalars(
            select(Member).where(Member.id.in_(participants), Member.room_id == room_id)
        )
    }
    missing = [p for p in participants if p not in known]
    if missing:
        raise LedgerError(f"Người tham gia không tồn tại: {missing}.")

    # split_shares raises MoneyError on any invalid split — let it propagate.
    shares = split_shares(total_amount, participants, adjustments, payer_id=payer_member_id)

    meal = Meal(
        room_id=room_id,
        occurred_on=occurred_on or today_ict(),
        payer_member_id=payer_member_id,
        total_amount=total_amount,
        note=note,
        raw_input=raw_input,
        source=source,
        logged_by=logged_by,
    )
    meal.shares = [
        MealShare(member_id=mid, share_amount=amt) for mid, amt in shares.items()
    ]
    session.add(meal)
    session.flush()  # assign meal.id

    return {
        "meal_id": meal.id,
        "occurred_on": meal.occurred_on.isoformat(),
        "payer_member_id": payer_member_id,
        "total_amount": total_amount,
        "shares": {mid: amt for mid, amt in shares.items()},
    }


def void_meal(session: Session, meal_id: int, *, room_id: int, by: str | None = None) -> dict:
    """Soft-delete a meal for a correction (design 6.1: void, then re-record)."""
    meal = session.get(Meal, meal_id)
    if meal is None or meal.room_id != room_id:
        raise LedgerError(f"Không tìm thấy bữa ăn #{meal_id}.")
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
    ``room_id`` — other rooms' meals never contribute.
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
    return out


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
