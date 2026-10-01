"""Draft-card lifecycle: persist, edit, commit, cancel — generic over the packs'
draft kinds (plan Task 3.4).

A draft is a ``RoomMessage`` whose ``kind`` is one of the enabled packs'
:class:`kernos.packs.DraftKind`\ s and whose ``attachments`` carry the proposed
payload plus a ``status`` (pending|committed|cancelled|superseded). Multiple drafts
may be pending in a room at once — proposals persist as independent cards, each
confirmed, edited or cancelled from its own card — with one exception: re-proposing
*the same thing* marks the older card ``superseded`` (see
:func:`_supersede_duplicates`). This module knows no business: what a payload
means, how it becomes ledger rows and what the confirmation card says are the
kind's ``prepare``/``commit``/``card``; the kinds are registered by the kernel
(:func:`set_draft_kinds`). The LLM never writes.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chat, ledger
from app.models import Meal, Member, Payment, RoomMessage
from kernos.packs import DraftKind
from ledger_core import drafts as core
from ledger_core.drafts import DRAFT_KINDS  # noqa: F401  (the lunch kinds, re-exported)

# The payload half of a draft lives in ``ledger_core.drafts`` (plan Task 3.2); these
# names stay for the modules and tests that import them from here.
_EDITABLE = core.EDITABLE
_sync_items = core.sync_items
_int_or_none = core.int_or_none
_draft_signature = core.draft_signature
_adjustments_map = core.adjustments_map

_kinds: dict[str, DraftKind] | None = None


def set_draft_kinds(kinds: dict[str, DraftKind] | None) -> None:
    """The kernel registers the enabled packs' draft kinds here (``Kernel.register_packs``)."""
    global _kinds
    _kinds = dict(kinds) if kinds is not None else None


def draft_kinds() -> dict[str, DraftKind]:
    if _kinds is None:
        from app.packs import host_packs
        set_draft_kinds({k: dk for p in host_packs() for k, dk in p.draft_kinds().items()})
    return _kinds


def _kind_of(m: RoomMessage) -> DraftKind | None:
    return draft_kinds().get(m.kind) if m is not None else None


def _supersede_duplicates(session: Session, room_id: int, new_att: dict) -> list[RoomMessage]:
    """Mark pending drafts that ``new_att`` re-proposes as ``superseded``.

    WHY — a pending draft blocks ``settle_period``, and nothing used to retire
    one. In production a 324,000đ proposal was refined into a 324,200đ proposal
    twenty messages later; confirming the new card left the old one pending, so
    every "chốt kỳ" ("close the period") for the next four hours answered
    "#101 chưa xác nhận" ("#101 not confirmed") while
    the stale card sat far above the fold. Four people asked the bot to close it
    and it had no way to.

    This writes NOTHING to the ledger. An earlier version of this module
    auto-*committed* the superseded draft (recording money nobody confirmed) and
    was rightly removed in 641ffa7; retiring an unconfirmed card is the safe
    half. Same payer + same participants + same day counts as the same
    proposal — two genuinely different meals for that exact group on one day are
    rare, and the cost of being wrong is a card the user can ask for again.
    """
    kind = draft_kinds()[new_att["type"]]
    if kind.signature is None:
        return []
    signature = kind.signature(new_att)
    superseded: list[RoomMessage] = []
    for m in list_pending_drafts(session, room_id):
        if m.kind != kind.kind:
            continue
        att = dict(m.attachments or {})
        if kind.signature(att) != signature:
            continue
        att["status"] = "superseded"
        m.attachments = att   # reassign so SQLAlchemy marks the JSON dirty
        superseded.append(m)
    if superseded:
        session.flush()
    return superseded


def create_card(session: Session, room_id: int, kind: str, payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """Persist a new pending card of ``kind``, retiring any it re-proposes.

    Returns ``(new_draft, superseded)`` — the caller publishes the superseded
    cards so their buttons disappear in every open client.
    """
    dk = draft_kinds().get(kind)
    if dk is None:
        raise ValueError(f"unknown draft kind {kind!r}")
    att = {"type": kind, "status": "pending", **payload}
    if dk.prepare is not None:
        att = dk.prepare(att)
    att.pop("logged_by", None)
    superseded = _supersede_duplicates(session, room_id, att)
    # A kind that says how it reads gets a readable body, so a client that does not yet
    # know the kind shows the text rather than an empty bubble (plan Phase 10.3).
    body = dk.body(att) if dk.body is not None else ""
    new_draft = chat.post_message(session, room_id, None, body=body, attachments=att, kind=kind)
    return new_draft, superseded


def create_draft(session: Session, room_id: int, payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """A pending ``expense_draft`` (see :func:`create_card`)."""
    return create_card(session, room_id, "expense_draft", payload)


def list_pending_drafts(session: Session, room_id: int) -> list[RoomMessage]:
    """All pending draft cards in the room, oldest first."""
    rows = session.scalars(
        select(RoomMessage)
        .where(RoomMessage.room_id == room_id, RoomMessage.kind.in_(list(draft_kinds())))
        .order_by(RoomMessage.id)
    ).all()
    return [m for m in rows if (m.attachments or {}).get("status") == "pending"]


def update_draft(session: Session, draft_id: int, room_id: int, patch: dict) -> RoomMessage:
    m = session.get(RoomMessage, draft_id)
    dk = _kind_of(m)
    if m is None or m.room_id != room_id or dk is None:
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("This draft has already been processed.")
    if patch.get("status") == "cancelled":
        att["status"] = "cancelled"
    else:
        for k in dk.editable:
            if k in patch:
                att[k] = patch[k]
        if dk.prepare is not None:
            dk.prepare(att)
    m.attachments = att   # reassign so SQLAlchemy marks the JSON dirty
    session.flush()
    return m


def _commit(session: Session, draft_id: int, room_id: int, logged_by: str | None,
            *, expect: str | None) -> RoomMessage:
    """The one commit path: the kind's ``commit`` writes the domain rows, its
    ``card`` says what happened, the draft flips to ``committed``."""
    m = session.get(RoomMessage, draft_id)
    dk = _kind_of(m)
    if m is None or m.room_id != room_id or dk is None or (expect is not None and m.kind != expect):
        raise ledger.LedgerError(f"Draft #{draft_id} not found.")
    att = dict(m.attachments or {})
    if att.get("status") != "pending":
        raise ledger.LedgerError("This draft has already been processed.")
    try:
        res = dk.commit(session, room_id, att, logged_by=logged_by)
    except ledger.LedgerError:
        raise
    except ValueError as exc:
        # A kind whose commit refuses (a configuration change the publish gates rejected)
        # is a refused commit like any other: the card stays pending and the route answers
        # 409, so it can be confirmed again once the reason is fixed (Phase 8 review F4).
        raise ledger.LedgerError(str(exc)) from exc
    body, card_att = dk.card(session, room_id, att, res) if dk.card else ("", None)
    card = chat.post_message(session, room_id, None, body, attachments=card_att, kind="bot")

    att["status"] = "committed"
    if isinstance(res, dict) and res.get("meal_id") is not None:
        att["committed_meal_id"] = res["meal_id"]
    m.attachments = att
    session.flush()
    return card


def commit_draft(session: Session, draft_id: int, room_id: int, logged_by: str | None) -> RoomMessage:
    """Commit a pending ``expense_draft``; returns the committed-meal card."""
    return _commit(session, draft_id, room_id, logged_by, expect="expense_draft")


def _repoint_or_void_edited_payment(session: Session, room_id: int, pay: Payment, res: dict) -> None:
    """One payment that was targeted at the meal being edited: it follows the
    edit only if the debt it was paying is still there to be paid.

    ``res`` is the freshly re-recorded meal's :func:`record_meal` result. A
    payment's ``(from_member_id, to_member_id)`` still matches an edge on the
    new meal iff ``to_member_id`` is the new payer and ``from_member_id`` is a
    participant with a nonzero share who isn't the payer — exactly the
    condition :func:`build_debt_edges` uses to emit an edge. When that holds we
    repoint the payment to the new meal id, same as before. When it doesn't
    (the edit changed the payer, or dropped this debtor from the meal), the
    money the payment names was handed to someone who is no longer the
    creditor for anything on this meal. Leaving it targetless would spill it
    into that pair's pool (``apply_payments_fifo``) as an unexplained credit —
    which is exactly how a real 60,000đ quick-pay, after its meal's payer was
    edited, went on to silently cancel a completely different 55,000đ debt
    between the same two people. So instead we withdraw it visibly: void the
    payment (it stays in the ledger, just inert) and tell the room, so a human
    decides whether to re-record it rather than the ledger deciding for them.
    """
    still_owed = (
        pay.to_member_id == res["payer_member_id"]
        and pay.from_member_id != res["payer_member_id"]
        and res["shares"].get(pay.from_member_id, 0) != 0
    )
    if still_owed:
        pay.meal_id = res["meal_id"]
        return
    pay.voided = True
    names = {mm.id: mm.display_name for mm in session.scalars(
        select(Member).where(Member.id.in_({pay.from_member_id, pay.to_member_id}))
    )}
    from_name = names.get(pay.from_member_id, "?")
    to_name = names.get(pay.to_member_id, "?")
    body = (
        f"Voided {from_name}'s {pay.amount:,}đ to {to_name} — after this edit that debt "
        f"is no longer on the meal. Re-record it if the money really did change hands."
    )
    chat.post_message(session, room_id, None, body, kind="bot")


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
            "This meal has been settled — record a new adjustment instead of editing it."
        )
    for k in _EDITABLE:
        if k in patch:
            att[k] = patch[k]
    # Payments quick-paid against this meal (⑦), read BEFORE the void: `void_meal`
    # with `untarget_payments=False` leaves them stamped with the old meal id, so
    # this is the only chance to see which ones need a decision below.
    targeted_payments = session.scalars(
        select(Payment).where(Payment.room_id == room_id, Payment.meal_id == meal.id,
                              Payment.voided.is_(False))
    ).all()
    # An edit is a void + re-record under a new id. The old `void_meal` call used
    # to un-target these payments itself (`untarget_payments` defaults True), which
    # dumped them into the pair pool before the new meal — and the debt it might or
    # might not still owe — existed. `untarget_payments=False` leaves that decision
    # to `_repoint_or_void_edited_payment` below, once `res` is known.
    ledger.void_meal(session, meal.id, room_id=room_id, by=logged_by, untarget_payments=False)
    # The re-record keeps the original meal's date and, as before the extraction,
    # does not carry the card's `place_id` (the recommit route never did).
    res = core.record_meal_payload(session, room_id, {**att, "place_id": None},
                                   logged_by=logged_by, occurred_on=meal.occurred_on)
    for pay in targeted_payments:
        _repoint_or_void_edited_payment(session, room_id, pay, res)
    body, card_att = draft_kinds()["expense_draft"].card(
        session, room_id, att, {**res, "replaces_meal_id": meal.id})
    meal_msg = chat.post_message(session, room_id, None, body, attachments=card_att, kind="bot")
    att["committed_meal_id"] = res["meal_id"]
    m.attachments = att
    session.flush()
    return meal_msg


def create_payment_draft(session: Session, room_id: int,
                         payload: dict) -> tuple[RoomMessage, list[RoomMessage]]:
    """A pending ``payment_draft`` (see :func:`create_card`): "tôi đã trả tiền Emi"
    ("I paid Emi") asked twice in a row leaves one live card, not two, and neither can go on
    blocking a settle."""
    return create_card(session, room_id, "payment_draft", payload)


def commit_payment_draft(session: Session, draft_id: int, room_id: int,
                         logged_by: str | None) -> RoomMessage:
    """Commit a pending ``payment_draft``; returns the recorded-payment card."""
    return _commit(session, draft_id, room_id, logged_by, expect="payment_draft")


def commit_any(session: Session, draft_id: int, room_id: int,
               logged_by: str | None) -> RoomMessage:
    """Commit a draft of any registered kind — the pack's ``DraftKind`` decides."""
    return _commit(session, draft_id, room_id, logged_by, expect=None)
