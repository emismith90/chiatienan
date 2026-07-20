import pytest
from app import accounts, rooms
from app.db import Database


def _room():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = rooms.create_room(s, "A"); rid = r.id
    return d, rid


def test_join_then_identify_and_token_maps_back():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        m, tok = accounts.create_account(s, room, display_name="An", nickname="an", pin="1234",
                                         bank_code="VCB", account_number="1", account_holder="AN")
        mid = m.id
    with d.session() as s:
        assert accounts.member_for_token(s, tok).id == mid
        room = rooms.room_by_id(s, rid)
        assert accounts.identify(s, room, nickname="an", pin="1234")
        assert accounts.identify(s, room, nickname="an", pin="0000") is None


def test_duplicate_nickname_rejected():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        accounts.create_account(s, room, display_name="An", nickname="an", pin="1",
                                bank_code=None, account_number=None, account_holder=None)
        with pytest.raises(accounts.AccountError):
            accounts.create_account(s, room, display_name="An2", nickname="an", pin="2",
                                    bank_code=None, account_number=None, account_holder=None)


def test_identify_claims_unclaimed_account_then_rejects_wrong_pin():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        m = accounts.add_unclaimed(s, room, display_name="Bình", nickname="binh",
                                   bank_code="VCB", account_number="2", account_holder="BINH")
        mid = m.id
        assert m.pin is None

    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        tok = accounts.identify(s, room, nickname="binh", pin="9999")
        assert tok is not None
        assert accounts.member_for_token(s, tok).id == mid

    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        # PIN is now claimed as "9999" — a wrong PIN must fail.
        assert accounts.identify(s, room, nickname="binh", pin="0000") is None
        # The original claiming PIN still works.
        assert accounts.identify(s, room, nickname="binh", pin="9999") is not None


def test_add_unclaimed_rejects_duplicate_nickname():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        accounts.add_unclaimed(s, room, display_name="An", nickname="an")
        with pytest.raises(accounts.AccountError):
            accounts.add_unclaimed(s, room, display_name="An2", nickname="an")
