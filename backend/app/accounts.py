"""Accounts + device sessions (join / identify / profile).

A ``Member`` with ``pin is None`` is "unclaimed" — added by the agent (or an
admin) as a placeholder for someone who hasn't joined yet. ``identify`` lets
that person claim the account: give the right nickname and any PIN, and it
becomes their PIN going forward. Once claimed (``pin`` set), ``identify``
behaves like a normal login and only succeeds on an exact PIN match.
"""
from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member, Room
from app.models import Session as UserSession


class AccountError(ValueError):
    pass


def _new_session(session: Session, member: Member) -> str:
    tok = secrets.token_urlsafe(24)
    session.add(UserSession(member_id=member.id, token=tok))
    session.flush()
    return tok


def _member_by_nickname(session: Session, room_id: int, nickname: str) -> Member | None:
    return session.scalars(
        select(Member).where(Member.room_id == room_id, Member.nickname == (nickname or "").strip())
    ).first()


def create_account(session: Session, room: Room, *, display_name, nickname, pin,
                    bank_code, account_number, account_holder) -> tuple[Member, str]:
    nickname = (nickname or "").strip()
    if not nickname or not (pin or "").strip():
        raise AccountError("Nickname and PIN are required.")
    if _member_by_nickname(session, room.id, nickname):
        raise AccountError(f"Nickname '{nickname}' is already taken in this room.")
    m = Member(
        room_id=room.id,
        display_name=(display_name or nickname).strip(),
        nickname=nickname,
        pin=str(pin).strip(),
        bank_code=bank_code,
        account_number=account_number,
        account_holder=account_holder,
    )
    session.add(m)
    session.flush()
    return m, _new_session(session, m)


def identify(session: Session, room: Room, *, nickname, pin) -> str | None:
    """Log in, claiming an unclaimed account if this is its first login.

    - Member not found → None.
    - Member found, ``pin is None`` (unclaimed) → claim it with the given
      PIN and return a fresh session token.
    - Member found, ``pin`` set → token only on an exact match, else None.
    """
    m = _member_by_nickname(session, room.id, nickname)
    if m is None:
        return None
    pin = str(pin).strip()
    if m.pin is None:
        m.pin = pin
        session.flush()
        return _new_session(session, m)
    if m.pin != pin:
        return None
    return _new_session(session, m)


def member_for_token(session: Session, token: str) -> Member | None:
    us = session.scalars(select(UserSession).where(UserSession.token == token)).first()
    return session.get(Member, us.member_id) if us else None


def update_profile(session: Session, member: Member, **fields) -> Member:
    for k in ("display_name", "bank_code", "account_number", "account_holder"):
        if k in fields and fields[k] is not None:
            setattr(member, k, fields[k])
    session.flush()
    return member


def add_unclaimed(session: Session, room: Room, *, display_name, nickname,
                   bank_code=None, account_number=None, account_holder=None) -> Member:
    """Add a placeholder member (no PIN yet) — e.g. via the ``add_member`` agent tool.

    Whoever later runs ``identify`` with this nickname claims the account.
    """
    nickname = (nickname or "").strip()
    if not nickname:
        raise AccountError("Nickname is required.")
    if _member_by_nickname(session, room.id, nickname):
        raise AccountError(f"Nickname '{nickname}' is already taken in this room.")
    m = Member(
        room_id=room.id,
        display_name=(display_name or nickname).strip(),
        nickname=nickname,
        pin=None,
        bank_code=bank_code,
        account_number=account_number,
        account_holder=account_holder,
    )
    session.add(m)
    session.flush()
    return m
