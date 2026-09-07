"""Draft payloads: what a card proposes, and how it becomes ledger rows.

The *persistence* of a draft (as a chat card the user confirms) is the host's;
this module owns everything about the payload itself — normalising itemised
splits, recognising a re-proposal, the editable field list, and turning a
confirmed payload into ``record_meal`` / ``record_payment`` calls. Nothing here
writes without a caller that has a confirmed card in hand (design D3).
"""
from __future__ import annotations

from datetime import date

from ledger_core import ledger
from ledger_core.money import MoneyError, itemized_adjustments, normalize_items, prorate_items

DRAFT_KINDS = ("expense_draft", "payment_draft")

EDITABLE = {
    "payer_member_id", "member_participants", "guests", "bill_total",
    "adjustments", "items", "discount_split", "dish", "initiator", "note",
    # The place guess rides the card the user already confirms (D3), so a wrong
    # one is a single tap to fix rather than a separate approval flow.
    "place_id",
}


def sync_items(att: dict) -> dict:
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
        raise MoneyError("Per-item split does not support cash guests yet — drop the guests or split evenly.")
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


def int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def draft_signature(att: dict) -> tuple:
    """What a draft proposes, reduced to an identity for spotting a re-proposal.

    The amount is deliberately NOT part of it: a re-proposal is usually a
    *correction* of the amount (production had 324,000đ re-proposed as
    324,200đ), so keying on it would miss every case worth catching.
    """
    if att.get("type") == "payment_draft":
        return ("payment", frozenset(
            (int_or_none(t.get("from_member_id")), int_or_none(t.get("to_member_id")))
            for t in att.get("transfers") or []
        ))
    return (
        "meal",
        int_or_none(att.get("payer_member_id")),
        frozenset(int_or_none(x) for x in att.get("member_participants") or []),
        att.get("occurred_on"),
    )


def adjustments_map(att: dict) -> dict[int, int]:
    return {int(a["member"]): int(a["amount"]) for a in att.get("adjustments") or []}


def require_meal_fields(att: dict) -> None:
    if (att.get("payer_member_id") is None or att.get("bill_total") is None
            or not att.get("member_participants")):
        raise ledger.LedgerError("The draft is missing required fields to record.")


def record_meal_payload(session, room_id: int, att: dict, *, logged_by: str | None,
                        occurred_on: date | None = None) -> dict:
    """``record_meal`` from a confirmed expense-draft payload (``sync_items`` first)."""
    sync_items(att)  # authoritative recompute: the card's items win over stored adjustments
    if occurred_on is None and att.get("occurred_on"):
        occurred_on = date.fromisoformat(att["occurred_on"])
    return ledger.record_meal(
        session,
        room_id=room_id,
        payer_member_id=int(att["payer_member_id"]),
        participants=[int(x) for x in att["member_participants"]],
        total_amount=int(att["bill_total"]),
        adjustments=adjustments_map(att),
        guests=[str(g) for g in att.get("guests") or []],
        dish=att.get("dish"),
        place_id=att.get("place_id"),
        initiator=att.get("initiator"),
        note=att.get("note"),
        occurred_on=occurred_on,
        raw_input=att.get("raw_input"),
        logged_by=logged_by,
    )


def require_transfers(att: dict) -> list[dict]:
    transfers = att.get("transfers") or []
    if not transfers:
        raise ledger.LedgerError("The draft has no transfers to record.")
    for t in transfers:
        if t.get("from_member_id") is None or t.get("to_member_id") is None or not t.get("amount"):
            raise ledger.LedgerError("A transfer is missing required fields.")
    return transfers


def record_payment_transfers(session, room_id: int, transfers: list[dict], *, logged_by: str | None) -> None:
    for t in transfers:
        ledger.record_payment(
            session, room_id=room_id,
            from_member_id=int(t["from_member_id"]), to_member_id=int(t["to_member_id"]),
            amount=int(t["amount"]), note=t.get("note"), logged_by=logged_by,
        )
