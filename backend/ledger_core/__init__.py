"""ledger_core — the money domain lunch and poker share (design §7.3, plan Task 3.2).

Members with bank details, cash payments between members, debt edges with FIFO
payment application, transfer netting, VietQR, periods, statements, settlements
and the two-step draft payload. A pack adds what a business *adds* to this; the
host owns members, rooms and messages and hands this package its member model
and its clock once, via :func:`configure`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from ledger_core import clock as _clock
from ledger_core import members as _members
from ledger_core.members import MemberDirectory, SqlMemberDirectory  # noqa: F401
from ledger_core.models import Base, Meal, MealShare, Payment, Settlement  # noqa: F401
from ledger_core.schema import bind  # noqa: F401

__version__ = "0.1.0"


def configure(*, member_model: type | None = None, directory: MemberDirectory | None = None,
              space_attr: str = "room_id", now: Callable[[], datetime] | None = None) -> None:
    """Bind the host's member model (or a directory of your own) and its clock."""
    if directory is None and member_model is not None:
        directory = SqlMemberDirectory(member_model, space_attr=space_attr)
    if directory is not None:
        _members.set_directory(directory)
    if now is not None:
        _clock.set_provider(now)
