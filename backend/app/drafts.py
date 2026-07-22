"""Expense-draft lifecycle: persist, edit, commit, cancel.

A draft is a ``RoomMessage`` (``kind="expense_draft"``) whose ``attachments``
carry the proposed meal plus a ``status`` (pending|committed|cancelled).
Multiple drafts may be pending in a room at once — proposals persist as
independent cards until each is confirmed, edited, or cancelled from its own
card; creating a new draft never touches an existing one. All ledger writes
go through :func:`app.ledger.record_meal` — the LLM never writes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat, ledger
from app.clock import today_ict
from app.models import Meal, Member, RoomMessage
from app.periods import resolve_period

DRAFT_KINDS = ("expense_draft", "payment_draft")

_EDITABLE = {
    "payer_member_id", "member_participants", "guests", "bill_total",
    "adjustments", "dish", "initiator", "note",
}


def create_draft(session: Session, room_id: int, payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """Persist a new pending draft. Never commits or supersedes an existing
    draft — proposals persist as independent cards until each is confirmed,
    edited, or cancelled from its own card. Returns ``(new_draft, [])``; the
    empty list preserves the caller signature (there are no supersede extras)."""
    att = {"type": "expense_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    new_draft = chat.post_message(session, room_id, None, body="", attachments=att, kind="expense_draft")
    return new_draft, []


def list_pending_drafts(session: Session, room_id: int) -> list[RoomMessage]:
    """All pending expense drafts in the room, oldest first."""
    rows = session.scalars(
        select(RoomMessage)
        .where(RoomMessage.room_id == room_id, RoomMessage.kind.in_(DRAFT_KINDS))
        .order_by(RoomMessage.id)
    ).all()
    return [m for m in rows if (m.attachments or {}).get("status") == "pending"]


def update_draft(session: Session, draft_id: int, room_id: int, patch: dict) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind not in DRAFT_KINDS:
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


def _meal_message(session: Session, room_id: int, att: dict, res: dict) -> RoomMessage:
    """Build + persist the committed-meal bot card from a record_meal result."""
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
    return chat.post_message(session, room_id, None, body, attachments=meal_att, kind="bot")


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
        occurred_on=date.fromisoformat(att["occurred_on"]) if att.get("occurred_on") else None,
        raw_input=att.get("raw_input"),
        logged_by=logged_by,
    )
    meal_msg = _meal_message(session, room_id, att, res)

    att["status"] = "committed"
    att["committed_meal_id"] = res["meal_id"]
    m.attachments = att
    session.flush()
    return meal_msg


def recommit_draft(session: Session, draft_id: int, room_id: int, patch: dict,
                    logged_by: str | None) -> RoomMessage:
    """Edit an already-committed draft: void its meal and re-record with the
    edited fields (ledger stays append-only). Rejected if the meal is inside a
    committed settlement — the closed period's numbers must not shift."""
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "committed" or not att.get("committed_meal_id"):
        raise ledger.LedgerError("Only a recorded draft can be edited.")
    meal = session.get(Meal, att["committed_meal_id"])
    if meal is None or meal.voided:
        raise ledger.LedgerError("The recorded meal is missing or already voided.")
    last = ledger.last_settlement(session, room_id)
    if last is not None and meal.occurred_on <= last.period_to:
        raise ledger.LedgerError(
            "Bữa ăn này đã được chốt — hãy ghi một khoản điều chỉnh mới thay vì sửa."
        )
    for k in _EDITABLE:
        if k in patch:
            att[k] = patch[k]
    ledger.void_meal(session, meal.id, room_id=room_id, by=logged_by)
    res = ledger.record_meal(
        session, room_id=room_id, payer_member_id=int(att["payer_member_id"]),
        participants=[int(x) for x in att["member_participants"]],
        total_amount=int(att["bill_total"]), adjustments=_adjustments_map(att),
        guests=[str(g) for g in att.get("guests") or []], dish=att.get("dish"),
        initiator=att.get("initiator"), note=att.get("note"),
        raw_input=att.get("raw_input"), logged_by=logged_by, occurred_on=meal.occurred_on,
    )
    meal_msg = _meal_message(session, room_id, att, res)
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


def create_payment_draft(session: Session, room_id: int, payload: dict) -> RoomMessage:
    att = {"type": "payment_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    return chat.post_message(session, room_id, None, body="", attachments=att, kind="payment_draft")


def commit_payment_draft(session: Session, draft_id: int, room_id: int,
                         logged_by: str | None) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "payment_draft":
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("This draft has already been processed.")
    transfers = att.get("transfers") or []
    if not transfers:
        raise ledger.LedgerError("The draft has no transfers to record.")
    for t in transfers:
        if t.get("from_member_id") is None or t.get("to_member_id") is None or not t.get("amount"):
            raise ledger.LedgerError("A transfer is missing required fields.")
    for t in transfers:
        ledger.record_payment(
            session, room_id=room_id,
            from_member_id=int(t["from_member_id"]), to_member_id=int(t["to_member_id"]),
            amount=int(t["amount"]), note=t.get("note"), logged_by=logged_by,
        )
    names = _all_member_names(session, room_id)
    pay_att = {
        "type": "payment",
        "transfers": [
            {"from": {"id": t["from_member_id"], "name": names.get(t["from_member_id"], "?")},
             "to": {"id": t["to_member_id"], "name": names.get(t["to_member_id"], "?")},
             "amount": t["amount"]}
            for t in transfers
        ],
        "balances": current_balances(session, room_id),
    }
    card = chat.post_message(session, room_id, None, chat._payment_body(pay_att),
                            attachments=pay_att, kind="bot")
    att["status"] = "committed"
    m.attachments = att
    session.flush()
    return card


def commit_any(session: Session, draft_id: int, room_id: int,
               logged_by: str | None) -> RoomMessage:
    """Commit a draft, dispatching by kind (meal vs payment)."""
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id:
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    if m.kind == "payment_draft":
        return commit_payment_draft(session, draft_id, room_id, logged_by)
    return commit_draft(session, draft_id, room_id, logged_by)
