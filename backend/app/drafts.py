"""Expense-draft lifecycle: persist, edit, commit, supersede, cancel.

A draft is a ``RoomMessage`` (``kind="expense_draft"``) whose ``attachments``
carry the proposed meal plus a ``status`` (pending|committed|cancelled). At most
one draft is ``pending`` per room: creating a new draft first commits the
existing pending one ("only when superseded"). All ledger writes go through
:func:`app.ledger.record_meal` — the LLM never writes.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat, ledger, roster
from app.clock import today_ict
from app.models import RoomMessage

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


def create_draft(session: Session, room_id: int, payload: dict) -> RoomMessage:
    """Commit any pending draft (supersede), then persist a new pending draft."""
    prev = get_pending_draft(session, room_id)
    if prev is not None:
        commit_draft(session, prev.id, room_id, logged_by=payload.get("logged_by"))
    att = {"type": "expense_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    return chat.post_message(session, room_id, None, body="", attachments=att, kind="expense_draft")


def update_draft(session: Session, draft_id: int, room_id: int, patch: dict) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Không tìm thấy thẻ nháp #{draft_id}.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("Thẻ nháp đã được xử lý.")
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


def commit_draft(session: Session, draft_id: int, room_id: int, logged_by: str | None) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    if m is None or m.room_id != room_id or m.kind != "expense_draft":
        raise ledger.LedgerError(f"Không tìm thấy thẻ nháp #{draft_id}.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("Thẻ nháp đã được xử lý.")

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
    names = {mem.id: mem.display_name for mem in roster.list_members(session, room_id)}
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
    from_date = last.period_to if last else None
    balances = ledger.period_balances(session, room_id, from_date, today_ict())
    names = {mem.id: mem.display_name for mem in roster.list_members(session, room_id)}
    rows = [{"id": mid, "name": names.get(mid, "?"), **vals} for mid, vals in balances.items()]
    return sorted(rows, key=lambda r: r["balance"], reverse=True)
