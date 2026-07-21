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


def test_find_member_by_id_and_nickname_incl_inactive():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        m = accounts.add_unclaimed(s, room, display_name="An", nickname="an")
        mid = m.id
        accounts.soft_delete_member(s, m)  # inactive
    with d.session() as s:
        assert accounts.find_member(s, rid, mid).id == mid          # by id
        assert accounts.find_member(s, rid, "an").id == mid          # by nickname, still findable
        assert accounts.find_member(s, rid, str(mid)).id == mid      # digit string -> id
        assert accounts.find_member(s, rid, "nope") is None


def test_soft_delete_deactivates_and_clears_sessions():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        m, tok = accounts.create_account(s, room, display_name="An", nickname="an", pin="1234",
                                         bank_code=None, account_number=None, account_holder=None)
        mid = m.id
        assert accounts.member_for_token(s, tok) is not None
        accounts.soft_delete_member(s, m)
    with d.session() as s:
        from app.models import Member
        assert s.get(Member, mid).active is False
        assert accounts.member_for_token(s, tok) is None            # signed out
        room = rooms.room_by_id(s, rid)
        assert accounts.identify(s, room, nickname="an", pin="1234") is None  # can't sign back in


def test_update_member_renames_with_uniqueness_and_restores():
    d, rid = _room()
    with d.session() as s:
        room = rooms.room_by_id(s, rid)
        a = accounts.create_account(s, room, display_name="An", nickname="an", pin="1",
                                    bank_code=None, account_number=None, account_holder=None)[0]
        b = accounts.add_unclaimed(s, room, display_name="Binh", nickname="binh")
        # rename b -> collides with an
        with pytest.raises(accounts.AccountError):
            accounts.update_member(s, b, nickname="an")
        # valid rename + detail edit
        accounts.update_member(s, b, nickname="binhh", display_name="Bình", bank_code="VCB")
        assert (b.nickname, b.display_name, b.bank_code) == ("binhh", "Bình", "VCB")
        # soft-delete then restore via update_member(active=True)
        accounts.soft_delete_member(s, a)
        assert a.active is False
        accounts.update_member(s, a, active=True)
        assert a.active is True
