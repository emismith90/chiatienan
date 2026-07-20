import pytest
from fastapi import HTTPException
from app import accounts, rooms, auth
from app.db import Database


def test_require_session_resolves_or_401(monkeypatch):
    d = Database("sqlite://"); d.create_all()
    monkeypatch.setattr(auth, "get_db", lambda: d)
    with d.session() as s:
        room = rooms.create_room(s, "A")
        _, tok = accounts.create_account(s, room, display_name="An", nickname="an", pin="1",
                                         bank_code=None, account_number=None, account_holder=None)
    ctx = auth.resolve_bearer(f"Bearer {tok}")
    assert ctx.nickname == "an"
    with pytest.raises(HTTPException):
        auth.resolve_bearer("Bearer nope")
