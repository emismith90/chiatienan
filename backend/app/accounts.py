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
    if m is None or not m.active:
        # Removed (soft-deleted) accounts can't be signed into or re-claimed
        # until an @bot `update_member active:true` restores them.
        return None
    pin = str(pin).strip()
    if m.pin is None:
        m.pin = pin
        session.flush()
        return _new_session(session, m)
    if m.pin != pin:
        return None
    return _new_session(session, m)


def find_member(session: Session, room_id: int, target) -> Member | None:
    """Resolve a member by numeric id or nickname, **including inactive** ones.

    Used by the update/delete member tools so a removed account can still be
    targeted (e.g. to fix its details or restore it). ``target`` may be an int,
    a digit string (treated as an id), or a nickname.
    """
    if isinstance(target, bool):  # guard: bool is an int subclass
        return None
    if isinstance(target, int) or (isinstance(target, str) and target.strip().isdigit()):
        m = session.get(Member, int(target))
        return m if m is not None and m.room_id == room_id else None
    return _member_by_nickname(session, room_id, str(target))


def soft_delete_member(session: Session, member: Member) -> Member:
    """Remove a member from the active roster without touching the ledger.

    Sets ``active = False`` (so they drop out of the roster, mentions, and join
    screen) and deletes their device sessions (signs them out). Their meals,
    shares, and settlements are left intact so historical balances stay correct.
    """
    member.active = False
    for us in session.scalars(select(UserSession).where(UserSession.member_id == member.id)):
        session.delete(us)
    session.flush()
    return member


def update_member(session: Session, member: Member, *, display_name=None, nickname=None,
                  bank_code=None, account_number=None, account_holder=None,
                  aliases=None, active=None) -> Member:
    """Edit a member's details (only non-None fields), or restore/deactivate it.

    A nickname change is validated for room-uniqueness (excluding this member),
    since ``(room_id, nickname)`` is unique whether or not the account is active.
    """
    if nickname is not None:
        nn = str(nickname).strip()
        if not nn:
            raise AccountError("Nickname cannot be empty.")
        clash = _member_by_nickname(session, member.room_id, nn)
        if clash is not None and clash.id != member.id:
            raise AccountError(f"Nickname '{nn}' is already taken in this room.")
        member.nickname = nn
    if display_name is not None and str(display_name).strip():
        member.display_name = str(display_name).strip()
    for key, val in (("bank_code", bank_code), ("account_number", account_number),
                     ("account_holder", account_holder)):
        if val is not None:
            setattr(member, key, val)
    if aliases is not None:
        member.aliases = list(aliases)
    if active is not None:
        member.active = bool(active)
    session.flush()
    return member


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
