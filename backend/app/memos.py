"""Memo drafts: a confirm card for writing lunch memory.

An observation is an *assertion* about a person or a business's quality, so
unlike a place row (which is inert until someone eats there) it goes through a
confirm card — design D7, and TODO.md's "no way to verify a false claim".

This reuses the ``RoomMessage`` + ``attachments`` + ``status`` **pattern** from
:mod:`app.drafts`, not its code: ``_sync_items``, ``prorate_items`` and the
itemised-split machinery are money-specific and have nothing to say about a note
that a restaurant is slow.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app import chat, observations
from app.models import RoomMessage

KIND = "memo_draft"


class MemoError(Exception):
    pass


def create(session: Session, room_id: int, *, action: str, subject: str,
           subject_label: str, text: str, when: date | None = None,
           gate: str | None = None, turn_id: str | None = None) -> RoomMessage:
    if action not in ("add", "remove"):
        raise MemoError(f"Unknown memo action {action!r}.")
    if not subject.startswith(("place:", "member:")):
        raise MemoError("Subject must be place:<slug> or member:<nickname>.")
    if not (text or "").strip():
        raise MemoError("A memo needs some text.")
    att = {
        "type": KIND,
        "action": action,
        "subject": subject,
        "subject_label": subject_label,
        "when": when.isoformat() if when else None,
        "gate": gate,
        "text": text.strip(),
        "status": "pending",
    }
    if turn_id:
        att["turn_id"] = turn_id
    return chat.post_message(session, room_id, None, body="", attachments=att, kind=KIND)


def _load(session: Session, memo_id: int, room_id: int) -> RoomMessage:
    m = session.get(RoomMessage, memo_id)
    if m is None or m.room_id != room_id or m.kind != KIND:
        raise MemoError("Memo not found.")
    return m


def commit(session: Session, memo_id: int, room_id: int) -> RoomMessage:
    """Apply a pending memo to the observations file. Idempotent."""
    m = _load(session, memo_id, room_id)
    att = dict(m.attachments or {})
    if att.get("status") == "committed":
        return m
    when = date.fromisoformat(att["when"]) if att.get("when") else None
    obs = observations.Observation(
        when=when, subject=att["subject"], gate=att.get("gate"), text=att["text"])
    if att["action"] == "add":
        observations.append(room_id, obs)
    else:
        observations.remove(room_id, subject=att["subject"], text=att["text"])
    att["status"] = "committed"
    m.attachments = att          # reassign so SQLAlchemy marks the JSON dirty
    session.flush()
    return m


def cancel(session: Session, memo_id: int, room_id: int) -> RoomMessage:
    m = _load(session, memo_id, room_id)
    att = dict(m.attachments or {})
    if att.get("status") == "pending":
        att["status"] = "cancelled"
        m.attachments = att
        session.flush()
    return m
