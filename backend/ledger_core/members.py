"""Who the members of a space are — a host concern the ledger only consults.

The ledger checks that a payer or participant exists in the space before writing
(review F3) and the roster resolves names, but the member table itself carries
auth (PINs, sessions) and stays with the host. The host registers its SQLAlchemy
member model once; :class:`SqlMemberDirectory` reads it. The model needs ``id``,
a space column (``room_id`` by default), ``display_name``, ``nickname``, ``aliases``,
``account_holder``, ``active``, ``default_participant``, and the bank fields the QR
builder reads.
"""
from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select


class MemberDirectory(Protocol):
    def get(self, session: Any, space_id: int, member_id: int) -> Any | None: ...
    def ids_in_space(self, session: Any, space_id: int, ids: list[int]) -> set[int]: ...
    def list(self, session: Any, space_id: int, *, include_inactive: bool = False,
             default_only: bool = False) -> list[Any]: ...
    def names(self, session: Any, space_id: int) -> dict[int, str]: ...


class SqlMemberDirectory:
    def __init__(self, model: type, *, space_attr: str = "room_id") -> None:
        self.model = model
        self._space = space_attr

    def _space_col(self):
        return getattr(self.model, self._space)

    def get(self, session, space_id, member_id):
        m = session.get(self.model, member_id)
        if m is None or getattr(m, self._space) != space_id:
            return None
        return m

    def ids_in_space(self, session, space_id, ids):
        if not ids:
            return set()
        return {m.id for m in session.scalars(
            select(self.model).where(self.model.id.in_(ids), self._space_col() == space_id))}

    def list(self, session, space_id, *, include_inactive=False, default_only=False):
        stmt = select(self.model).where(self._space_col() == space_id)
        if not include_inactive:
            stmt = stmt.where(self.model.active.is_(True))
        if default_only:
            stmt = stmt.where(self.model.default_participant.is_(True))
        return list(session.scalars(stmt.order_by(self.model.display_name)))

    def names(self, session, space_id):
        return {m.id: m.display_name for m in session.scalars(
            select(self.model).where(self._space_col() == space_id))}


class _Unconfigured:
    def __getattr__(self, name):
        raise RuntimeError("ledger_core has no member directory: call ledger_core.configure(member_model=...)")


_directory: Any = _Unconfigured()


def set_directory(directory: MemberDirectory) -> None:
    global _directory
    _directory = directory


def directory() -> MemberDirectory:
    return _directory
