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


def test_resolve_bearer_case_insensitive(monkeypatch):
    """Test case-insensitive Bearer header handling."""
    d = Database("sqlite://"); d.create_all()
    monkeypatch.setattr(auth, "get_db", lambda: d)
    with d.session() as s:
        room = rooms.create_room(s, "A")
        _, tok = accounts.create_account(s, room, display_name="An", nickname="an", pin="1",
                                         bank_code=None, account_number=None, account_holder=None)

    # Test with lowercase "bearer "
    ctx = auth.resolve_bearer(f"bearer {tok}")
    assert ctx.nickname == "an"

    # Test with uppercase "BEARER "
    ctx = auth.resolve_bearer(f"BEARER {tok}")
    assert ctx.nickname == "an"

    # Test with raw token (no prefix)
    ctx = auth.resolve_bearer(tok)
    assert ctx.nickname == "an"


def test_resolve_bearer_invalid_inputs(monkeypatch):
    """Test that invalid inputs raise HTTPException."""
    d = Database("sqlite://"); d.create_all()
    monkeypatch.setattr(auth, "get_db", lambda: d)
    with d.session() as s:
        room = rooms.create_room(s, "A")
        accounts.create_account(s, room, display_name="An", nickname="an", pin="1",
                                bank_code=None, account_number=None, account_holder=None)

    # Test with empty string
    with pytest.raises(HTTPException):
        auth.resolve_bearer("")

    # Test with None
    with pytest.raises(HTTPException):
        auth.resolve_bearer(None)
