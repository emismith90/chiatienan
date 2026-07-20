from app import rooms
from app.db import Database


def test_create_room_has_unguessable_token():
    d = Database("sqlite://"); d.create_all()
    with d.session() as s:
        r = rooms.create_room(s, "Lunch crew")
        assert r.name == "Lunch crew" and len(r.invite_token) >= 16
    with d.session() as s:
        assert rooms.room_by_invite(s, r.invite_token).id == r.id
        assert rooms.room_by_invite(s, "nope") is None
