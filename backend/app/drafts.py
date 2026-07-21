"""Expense-draft lifecycle: persist, edit, commit, supersede, cancel.

A draft is a ``RoomMessage`` (``kind="expense_draft"``) whose ``attachments``
carry the proposed meal plus a ``status`` (pending|committed|cancelled). At most
one draft is ``pending`` per room: creating a new draft first commits the
existing pending one ("only when superseded"), or — if that prior draft was
edited into an invalid state — cancels it instead so one bad draft can never
block all future proposals. All ledger writes go through
:func:`app.ledger.record_meal` — the LLM never writes.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat, ledger
from app.clock import today_ict
from app.models import Member, RoomMessage
from app.money import MoneyError
from app.periods import resolve_period

logger = logging.getLogger("chiatienan")

_EDITABLE = {
    "payer_member_id", "member_participants", "guests", "bill_total",
    "adjustments", "dish", "initiator", "note",
}


def get_pending_draft(session: Session, room_id: int) -> RoomMessage | None:
    for m in session.scalars(
        select(RoomMessage)
        .where(RoomMessage.room_id == room_id, RoomMessage.kind == "expense_draft")
        .order_by(RoomMessage.id.desc())
    ):
        if (m.attachments or {}).get("status") == "pending":
            return m
    return None


def create_draft(session: Session, room_id: int, payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """Commit any pending draft (supersede), then persist a new pending draft.

    Returns ``(new_draft, extras)``. ``extras`` is ``[]`` when there was no
    pending draft to supersede; otherwise it carries the messages produced by
    the supersede — ``[committed_prev_draft, superseded_meal_msg]`` on a clean
    commit, or ``[cancelled_prev_draft]`` if the prior draft could no longer be
    committed (edited into an invalid state) and was cancelled instead. The
    caller (``chat.run_bot_turn``) is responsible for publishing these over
    SSE, since ``commit_draft``'s DB writes alone don't reach live clients.
    """
    extras: list[RoomMessage] = []
    prev = get_pending_draft(session, room_id)
    if prev is not None:
        try:
            meal_msg = commit_draft(session, prev.id, room_id, logged_by=payload.get("logged_by"))
            extras = [prev, meal_msg]
        except (MoneyError, ledger.LedgerError) as exc:
            logger.warning(
                "supersede commit failed for draft #%s in room %s (%s) — cancelling instead",
                prev.id, room_id, exc,
            )
            att = dict(prev.attachments or {})
            att["status"] = "cancelled"
            prev.attachments = att
            session.flush()
            extras = [prev]
    att = {"type": "expense_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    new_draft = chat.post_message(session, room_id, None, body="", attachments=att, kind="expense_draft")
    return new_draft, extras


def update_draft(session: Session, draft_id: int, room_id: int, patch: dict) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("This draft has already been processed.")
    if patch.get("status") == "cancelled":
        att["status"] = "cancelled"
    else:
        for k in _EDITABLE:
            if k in patch:
                att[k] = patch[k]
    m.attachments = att   # reassign so SQLAlchemy marks the JSON dirty
    session.flush()
    return m


def _adjustments_map(att: dict) -> dict[int, int]:
    return {int(a["member"]): int(a["amount"]) for a in att.get("adjustments") or []}


def _all_member_names(session: Session, room_id: int) -> dict[int, str]:
    """Display names for EVERY member of the room, active or not.

    Unlike :func:`app.roster.list_members` (active-only, used for LLM-facing
    resolution), the meal/balances payloads shown to humans must still show a
    real name for a since-deactivated member instead of "?" — this is display
    only, the underlying balance math is unaffected.
    """
    return {m.id: m.display_name for m in session.scalars(select(Member).where(Member.room_id == room_id))}


def commit_draft(session: Session, draft_id: int, room_id: int, logged_by: str | None) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("This draft has already been processed.")
    if (att.get("payer_member_id") is None or att.get("bill_total") is None
            or not att.get("member_participants")):
        raise ledger.LedgerError("The draft is missing required fields to record.")

    res = ledger.record_meal(
        session,
        room_id=room_id,
        payer_member_id=int(att["payer_member_id"]),
        participants=[int(x) for x in att["member_participants"]],
        total_amount=int(att["bill_total"]),
        adjustments=_adjustments_map(att),
        guests=[str(g) for g in att.get("guests") or []],
        dish=att.get("dish"),
        initiator=att.get("initiator"),
        note=att.get("note"),
        raw_input=att.get("raw_input"),
        logged_by=logged_by,
    )
    names = _all_member_names(session, room_id)
    meal_att = {
        "type": "meal",
        "meal_id": res["meal_id"],
        "occurred_on": res["occurred_on"],
        "bill_total": res["bill_total"],
        "tracked_total": res["tracked_total"],
        "guests": res["guests"],
        "dish": att.get("dish"),
        "initiator": att.get("initiator"),
        "note": att.get("note"),
        "payer": {"id": res["payer_member_id"], "name": names.get(res["payer_member_id"], "?")},
        "shares": [{"id": mid, "name": names.get(mid, "?"), "amount": amt}
                   for mid, amt in res["shares"].items()],
        "balances": current_balances(session, room_id),
    }
    body = chat._meal_body(meal_att)
    meal_msg = chat.post_message(session, room_id, None, body, attachments=meal_att, kind="bot")

    att["status"] = "committed"
    att["committed_meal_id"] = res["meal_id"]
    m.attachments = att
    session.flush()
    return meal_msg


def current_balances(session: Session, room_id: int) -> list[dict]:
    last = ledger.last_settlement(session, room_id)
    period = resolve_period(
        "since_last",
        today=today_ict(),
        last_settlement_to=last.period_to if last else None,
    )
    balances = ledger.period_balances(session, room_id, period["from"], period["to"])
    names = _all_member_names(session, room_id)
    rows = [{"id": mid, "name": names.get(mid, "?"), **vals} for mid, vals in balances.items()]
    return sorted(rows, key=lambda r: r["balance"], reverse=True)
