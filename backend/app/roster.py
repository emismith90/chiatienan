"""Member roster: read + name/alias/mention resolution, room-scoped.

Rooms (design pivot: PWA replaces the Teams tenant) each carry their own
member list. Account creation/administration lives in ``accounts.py`` (a
later task); this module only lists and resolves the members of a given
room — no Teams identity capture, no ``teams_user_id`` / ``aad_object_id``.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member


def list_members(session: Session, room_id: int, *, include_inactive: bool = False) -> list[Member]:
    """Members of ``room_id``, ordered by display name.

    Excludes soft-deleted (``active=False``) members by default so they drop out
    of the roster, mentions, and new-meal selection. Pass
    ``include_inactive=True`` for name-resolution over historical data (past
    balances/settlements can still reference a since-removed member).
    """
    stmt = select(Member).where(Member.room_id == room_id)
    if not include_inactive:
        stmt = stmt.where(Member.active.is_(True))
    return list(session.scalars(stmt.order_by(Member.display_name)))


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def resolve(
    session: Session,
    room_id: int,
    *,
    names: list[str] | None = None,
    mentions: list[dict] | None = None,
    all_active: bool = False,
) -> dict:
    """Resolve free-text names / ``@mentions`` (+ ``all_active``) into member ids
    within ``room_id``.

    ``mentions`` are ``{"nickname": ...}`` dicts resolved within the room. A
    name matches on ``display_name``, ``nickname``, or any alias
    (case-insensitive) among the room's active members. Returns
    ``{"matched": [{"id", "display_name"}], "unresolved": [<raw strings>]}``.
    ``all_active=True`` returns every active room member so the LLM never has
    to enumerate the roster from memory (design §5).
    """
    members = list_members(session, room_id)

    name_index: dict[str, Member] = {}
    for m in members:
        name_index.setdefault(_norm(m.display_name), m)
        name_index.setdefault(_norm(m.nickname), m)
        for alias in m.aliases or []:
            name_index.setdefault(_norm(alias), m)

    matched: dict[int, Member] = {}
    unresolved: list[str] = []

    if all_active:
        for m in members:
            matched[m.id] = m

    for mention in mentions or []:
        nickname = mention.get("nickname")
        m = name_index.get(_norm(nickname)) if nickname else None
        if m is not None:
            matched[m.id] = m
        else:
            unresolved.append(nickname or "?")

    for raw in names or []:
        m = name_index.get(_norm(raw))
        if m is not None:
            matched[m.id] = m
        else:
            unresolved.append(raw)

    return {
        "matched": [{"id": m.id, "display_name": m.display_name} for m in matched.values()],
        "unresolved": unresolved,
    }
