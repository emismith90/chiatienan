"""Member roster: CRUD, name/alias/mention resolution, identity capture.

Admin manages the roster via ``/admin`` (bank details are error-prone to type in
chat). Teams identities (``teams_user_id`` / ``aad_object_id``) are captured
automatically the first time someone is seen, so nobody has to know their own
``29:…`` id — the admin just links a captured id to a person.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Member


def list_members(session: Session, *, active_only: bool = False) -> list[Member]:
    stmt = select(Member).order_by(Member.display_name)
    if active_only:
        stmt = stmt.where(Member.active.is_(True))
    return list(session.scalars(stmt))


def get_member(session: Session, member_id: int) -> Member | None:
    return session.get(Member, member_id)


def create_member(
    session: Session,
    *,
    display_name: str,
    aliases: list[str] | None = None,
    teams_user_id: str | None = None,
    aad_object_id: str | None = None,
    bank_code: str | None = None,
    account_number: str | None = None,
    account_holder: str | None = None,
    active: bool = True,
) -> Member:
    member = Member(
        display_name=display_name.strip(),
        aliases=[a.strip() for a in (aliases or []) if a.strip()],
        teams_user_id=teams_user_id,
        aad_object_id=aad_object_id,
        bank_code=bank_code,
        account_number=account_number,
        account_holder=account_holder,
        active=active,
    )
    session.add(member)
    session.flush()
    return member


def update_member(session: Session, member_id: int, **fields) -> Member:
    member = session.get(Member, member_id)
    if member is None:
        raise ValueError(f"member {member_id} not found")
    for key, value in fields.items():
        if hasattr(member, key):
            setattr(member, key, value)
    session.flush()
    return member


def member_by_teams_id(session: Session, teams_user_id: str) -> Member | None:
    return session.scalars(
        select(Member).where(Member.teams_user_id == teams_user_id)
    ).first()


def capture_sender(
    session: Session, *, teams_user_id: str, aad_object_id: str | None, name: str
) -> Member:
    """Ensure a member row exists for a seen Teams identity.

    If a member already carries this ``teams_user_id`` it is returned unchanged.
    Otherwise a stub member is created (``active=False``, no bank details) so it
    shows up in ``/admin`` for the admin to complete + activate. This is how the
    bot "captures the id on first mention" without anyone typing a ``29:…`` id.
    """
    existing = member_by_teams_id(session, teams_user_id)
    if existing is not None:
        if aad_object_id and not existing.aad_object_id:
            existing.aad_object_id = aad_object_id
            session.flush()
        return existing
    return create_member(
        session,
        display_name=(name or "Người dùng mới").strip(),
        teams_user_id=teams_user_id,
        aad_object_id=aad_object_id,
        active=False,
    )


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def resolve(
    session: Session,
    *,
    names: list[str] | None = None,
    mentions: list[dict] | None = None,
    all_active: bool = False,
) -> dict:
    """Resolve free-text names / ``@mentions`` (+ ``all_active``) into member ids.

    ``mentions`` are ``{"teams_user_id": ..., "name": ...}`` from the activity's
    ``<at>`` entities. Returns ``{"matched": [{id, display_name}], "unresolved":
    [<raw strings>]}``. A name matches on display_name or any alias
    (case-insensitive). ``all_active=True`` returns every active member so the LLM
    never has to enumerate the roster from memory (design §5).
    """
    members = list_members(session)
    by_teams = {m.teams_user_id: m for m in members if m.teams_user_id}

    # index names/aliases → member
    name_index: dict[str, Member] = {}
    for m in members:
        name_index[_norm(m.display_name)] = m
        for alias in m.aliases or []:
            name_index.setdefault(_norm(alias), m)

    matched: dict[int, Member] = {}
    unresolved: list[str] = []

    if all_active:
        for m in members:
            if m.active:
                matched[m.id] = m

    for mention in mentions or []:
        tid = mention.get("teams_user_id")
        m = by_teams.get(tid) if tid else None
        if m is not None:
            matched[m.id] = m
        else:
            unresolved.append(mention.get("name") or tid or "?")

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
