"""Expense-draft lifecycle: persist, edit, commit, cancel.

A draft is a ``RoomMessage`` (``kind="expense_draft"``) whose ``attachments``
carry the proposed meal plus a ``status``
(pending|committed|cancelled|superseded). Multiple drafts may be pending in a
room at once — proposals persist as independent cards, each confirmed, edited or
cancelled from its own card — with one exception: re-proposing *the same thing*
marks the older card ``superseded`` (see :func:`_supersede_duplicates`). All
ledger writes go through :func:`app.ledger.record_meal` — the LLM never writes.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat, ledger
from app.models import Meal, Member, RoomMessage
from app.money import MoneyError, itemized_adjustments, normalize_items, prorate_items

DRAFT_KINDS = ("expense_draft", "payment_draft")

_EDITABLE = {
    "payer_member_id", "member_participants", "guests", "bill_total",
    "adjustments", "items", "discount_split", "dish", "initiator", "note",
}


def _sync_items(att: dict) -> dict:
    """Re-derive ``adjustments`` from the itemized ``items``, in place.

    In itemized mode ``items`` (per-person list prices off the bill) is the
    single source of truth and ``adjustments`` is only its ledger encoding — so
    every write path recomputes it here rather than trusting whatever the client
    (or the model) sent. Editing a total, a price, or the guest list on the card
    therefore re-prorates the discount instead of leaving a stale split behind.

    No-op for an ordinary equal-split draft. Raises :class:`MoneyError` if the
    items no longer describe a valid split (e.g. a participant was added on the
    card without a price).
    """
    items = att.get("items")
    if not items:
        return att
    if att.get("guests"):
        raise MoneyError("Ghi theo món chưa hỗ trợ khách lẻ — bỏ khách ra hoặc chia đều.")
    participants = [int(x) for x in att.get("member_participants") or []]
    items = normalize_items(items, participants)
    # The card patches a fixed field list that does not include discount_split,
    # so it is read back off the draft: editing a price must not silently switch
    # an equal-delta split to proportional.
    shares = prorate_items(int(att.get("bill_total") or 0),
                           {i["member"]: i["amount"] for i in items},
                           discount_split=att.get("discount_split") or "proportional")
    att["items"] = items
    att["adjustments"] = [{"member": m, "amount": a}
                          for m, a in itemized_adjustments(int(att["bill_total"]), shares).items()]
    return att


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _draft_signature(att: dict) -> tuple:
    """What a draft proposes, reduced to an identity for spotting a re-proposal.

    The amount is deliberately NOT part of it: a re-proposal is usually a
    *correction* of the amount (production had 324,000đ re-proposed as
    324,200đ), so keying on it would miss every case worth catching.
    """
    if att.get("type") == "payment_draft":
        return ("payment", frozenset(
            (_int_or_none(t.get("from_member_id")), _int_or_none(t.get("to_member_id")))
            for t in att.get("transfers") or []
        ))
    return (
        "meal",
        _int_or_none(att.get("payer_member_id")),
        frozenset(_int_or_none(x) for x in att.get("member_participants") or []),
        att.get("occurred_on"),
    )


def _supersede_duplicates(session: Session, room_id: int, new_att: dict) -> list[RoomMessage]:
    """Mark pending drafts that ``new_att`` re-proposes as ``superseded``.

    WHY — a pending draft blocks ``settle_period``, and nothing used to retire
    one. In production a 324,000đ proposal was refined into a 324,200đ proposal
    twenty messages later; confirming the new card left the old one pending, so
    every "chốt kỳ" for the next four hours answered "#101 chưa xác nhận" while
    the stale card sat far above the fold. Four people asked the bot to close it
    and it had no way to.

    This writes NOTHING to the ledger. An earlier version of this module
    auto-*committed* the superseded draft (recording money nobody confirmed) and
    was rightly removed in 641ffa7; retiring an unconfirmed card is the safe
    half. Same payer + same participants + same day counts as the same
    proposal — two genuinely different meals for that exact group on one day are
    rare, and the cost of being wrong is a card the user can ask for again.
    """
    signature = _draft_signature(new_att)
    superseded: list[RoomMessage] = []
    for m in list_pending_drafts(session, room_id):
        att = dict(m.attachments or {})
        if _draft_signature(att) != signature:
            continue
        att["status"] = "superseded"
        m.attachments = att   # reassign so SQLAlchemy marks the JSON dirty
        superseded.append(m)
    if superseded:
        session.flush()
    return superseded


def create_draft(session: Session, room_id: int, payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """Persist a new pending draft, retiring any it re-proposes.

    Returns ``(new_draft, superseded)`` — the caller publishes the superseded
    cards so their buttons disappear in every open client.
    """
    att = _sync_items({"type": "expense_draft", "status": "pending", **payload})
    att.pop("logged_by", None)
    superseded = _supersede_duplicates(session, room_id, att)
    new_draft = chat.post_message(session, room_id, None, body="", attachments=att, kind="expense_draft")
    return new_draft, superseded


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
        _sync_items(att)
    m.attachments = att   # reassign so SQLAlchemy marks the JSON dirty
    session.flush()
    return m


def _adjustments_map(att: dict) -> dict[int, int]:
    return {int(a["member"]): int(a["amount"]) for a in att.get("adjustments") or []}


def _all_member_names(session: Session, room_id: int) -> dict[int, str]:
    """Display names for EVERY member of the room, active or not.

    Unlike :func:`app.roster.list_members` (active-only, used for LLM-facing
    resolution), the meal/payment payloads shown to humans must still show a
    real name for a since-deactivated member instead of "?" — this is display
    only, the underlying share math is unaffected.
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
        "items": att.get("items") or None,
        "payer": {"id": res["payer_member_id"], "name": names.get(res["payer_member_id"], "?")},
        "shares": [{"id": mid, "name": names.get(mid, "?"), "amount": amt}
                   for mid, amt in res["shares"].items()],
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
    _sync_items(att)  # authoritative recompute: the card's items win over stored adjustments

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
    _sync_items(att)
    ledger.void_meal(session, meal.id, room_id=room_id, by=logged_by)
    res = ledger.record_meal(
        session, room_id=room_id, payer_member_id=int(att["payer_member_id"]),
        participants=[int(x) for x in att["member_participants"]],
        total_amount=int(att["bill_total"]), adjustments=_adjustments_map(att),
        guests=[str(g) for g in att.get("guests") or []], dish=att.get("dish"),
        initiator=att.get("initiator"), note=att.get("note"),
        raw_input=att.get("raw_input"), logged_by=logged_by, occurred_on=meal.occurred_on,
    )
    # An edit is a void + re-record under a new id, so anything already paid
    # against the old meal (⑦ quick-pay) follows it — otherwise the payer's own
    # statement shows their share as unpaid while the settlement counts it.
    ledger.repoint_meal_payments(session, room_id=room_id, old_meal_id=meal.id,
                                 new_meal_id=res["meal_id"])
    meal_msg = _meal_message(session, room_id, att, res)
    att["committed_meal_id"] = res["meal_id"]
    m.attachments = att
    session.flush()
    return meal_msg


def create_payment_draft(session: Session, room_id: int,
                         payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """Persist a pending payment draft, retiring any it re-proposes.

    Same ``(new_draft, superseded)`` contract as :func:`create_draft`: "tôi đã
    trả tiền Emi" asked twice in a row leaves one live card, not two, and neither
    can go on blocking a settle.
    """
    att = {"type": "payment_draft", "status": "pending", **payload}
    att.pop("logged_by", None)
    superseded = _supersede_duplicates(session, room_id, att)
    new_draft = chat.post_message(session, room_id, None, body="", attachments=att, kind="payment_draft")
    return new_draft, superseded


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
