from app import roster
from app.models import Member, Room


def _make_room(s, name, token):
    room = Room(name=name, invite_token=token)
    s.add(room)
    s.flush()
    return room


def test_resolve_and_list_are_room_scoped(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        r2 = _make_room(s, "B", "b")
        s.add(Member(room_id=r1.id, display_name="An", nickname="an", pin="1", aliases=["cu An"]))
        s.add(Member(room_id=r2.id, display_name="Zed", nickname="zed", pin="9"))
        s.flush()

        got = roster.resolve(s, r1.id, names=["cu An"], mentions=[], all_active=False)
        assert len(got["matched"]) == 1
        assert got["matched"][0]["display_name"] == "An"
        assert got["unresolved"] == []

        assert [m.display_name for m in roster.list_members(s, r1.id)] == ["An"]
        # room B's roster is unaffected by room A's members
        assert [m.display_name for m in roster.list_members(s, r2.id)] == ["Zed"]


def test_resolve_by_display_name_and_nickname_case_insensitive(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        an = Member(room_id=r1.id, display_name="An", nickname="an-nick", pin="1")
        s.add(an)
        s.flush()

        by_display = roster.resolve(s, r1.id, names=["AN"])
        assert by_display["matched"][0]["id"] == an.id

        by_nick = roster.resolve(s, r1.id, names=["AN-NICK"])
        assert by_nick["matched"][0]["id"] == an.id


def test_resolve_name_from_other_room_is_unresolved(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        r2 = _make_room(s, "B", "b")
        s.add(Member(room_id=r2.id, display_name="Zed", nickname="zed", pin="9"))
        s.flush()

        got = roster.resolve(s, r1.id, names=["Zed"])
        assert got["matched"] == []
        assert got["unresolved"] == ["Zed"]


def test_resolve_all_active_returns_only_active_room_members(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        r2 = _make_room(s, "B", "b")
        s.add(Member(room_id=r1.id, display_name="An", nickname="an", pin="1"))
        s.add(Member(room_id=r1.id, display_name="Cu", nickname="cu", pin="2", active=False))
        s.add(Member(room_id=r2.id, display_name="Zed", nickname="zed", pin="9"))
        s.flush()

        got = roster.resolve(s, r1.id, all_active=True)
        names = {m["display_name"] for m in got["matched"]}
        assert names == {"An"}


def test_resolve_by_mention_nickname_within_room(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        an = Member(room_id=r1.id, display_name="An", nickname="an", pin="1")
        s.add(an)
        s.flush()

        got = roster.resolve(s, r1.id, mentions=[{"nickname": "an"}])
        assert got["matched"][0]["id"] == an.id

        # unknown mention nickname is unresolved
        got2 = roster.resolve(s, r1.id, mentions=[{"nickname": "ghost"}])
        assert got2["matched"] == []
        assert got2["unresolved"] == ["ghost"]


def test_resolve_by_mention_nickname_does_not_cross_rooms(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        r2 = _make_room(s, "B", "b")
        s.add(Member(room_id=r2.id, display_name="Zed", nickname="zed", pin="9"))
        s.flush()

        # "zed" only exists in room B; resolving inside room A must not match it
        got = roster.resolve(s, r1.id, mentions=[{"nickname": "zed"}])
        assert got["matched"] == []
        assert got["unresolved"] == ["zed"]


def test_teams_capture_helpers_are_removed():
    assert not hasattr(roster, "capture_sender")
    assert not hasattr(roster, "member_by_teams_id")
