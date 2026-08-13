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


def test_list_members_default_only_excludes_flagged_out_members(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        s.add(Member(room_id=r1.id, display_name="An", nickname="an", pin="1"))
        s.add(Member(room_id=r1.id, display_name="Cu", nickname="cu", pin="2", default_participant=False))
        s.flush()

        assert {m.display_name for m in roster.list_members(s, r1.id)} == {"An", "Cu"}
        assert {m.display_name for m in roster.list_members(s, r1.id, default_only=True)} == {"An"}


def test_resolve_all_active_skips_default_participant_false_but_name_still_resolves(db):
    with db.session() as s:
        r1 = _make_room(s, "A", "a")
        s.add(Member(room_id=r1.id, display_name="An", nickname="an", pin="1"))
        cu = Member(room_id=r1.id, display_name="Cu", nickname="cu", pin="2", default_participant=False)
        s.add(cu)
        s.flush()

        got = roster.resolve(s, r1.id, all_active=True)
        assert {m["display_name"] for m in got["matched"]} == {"An"}

        # Cu is opted out of "whole group" sweeps but still resolvable by name.
        got2 = roster.resolve(s, r1.id, names=["Cu"])
        assert got2["matched"][0]["id"] == cu.id


def _prod_room(s):
    """The production roster that exposed the bug (room "12B +2 🐔")."""
    r = _make_room(s, "12B", "t")
    s.add_all([
        Member(room_id=r.id, display_name="Emi", nickname="Emi", pin="1",
               account_holder="Le Hoang Hung"),
        Member(room_id=r.id, display_name="Tabu", nickname="Tabu", pin="2",
               aliases=["Bùi Trang", "Trang Bùi"], account_holder="BUI THU TRANG"),
        Member(room_id=r.id, display_name="Linh Nguyen", nickname="Linh", pin="3",
               account_holder="NGUYEN ANH LINH"),
        Member(room_id=r.id, display_name="Kun", nickname="Kun", pin="4",
               aliases=["Quách Trí Dũng", "Dũng"], account_holder="QUACH TRI DUNG"),
        Member(room_id=r.id, display_name="Nhím", nickname="Nhím", pin="5",
               aliases=["Trang Dinh"], account_holder="DINH HONG TRANG"),
        Member(room_id=r.id, display_name="Giang Hoàng", nickname="Giang", pin="6",
               account_holder="HOANG MINH GIANG"),
    ])
    s.flush()
    return r


def test_resolve_finds_the_real_name_behind_a_nickname(db):
    """"anh Hưng" is Emi — the only place "Hung" is written is the bank account.

    Production: "nay ăn bún cá với anh Hưng chị Nhím hết 175k" resolved only
    Nhím, so the bill was proposed split two ways instead of three.
    """
    with db.session() as s:
        r = _prod_room(s)
        for query in ("Hưng", "anh Hưng", "a Hưng", "hung", "Le Hoang Hung", "Hưng Lê"):
            got = roster.resolve(s, r.id, names=[query])
            assert [m["display_name"] for m in got["matched"]] == ["Emi"], query
            assert got["unresolved"] == []


def test_resolve_is_tone_insensitive_both_ways(db):
    with db.session() as s:
        r = _prod_room(s)
        for query, expected in (("Nhim", "Nhím"), ("chị Nhím", "Nhím"),
                                ("Dũng", "Kun"), ("dung", "Kun"),
                                ("Giang", "Giang Hoàng"), ("hoang", "Giang Hoàng")):
            got = roster.resolve(s, r.id, names=[query])
            assert [m["display_name"] for m in got["matched"]] == [expected], query


def test_resolve_matches_the_bank_account_holder(db):
    with db.session() as s:
        r = _prod_room(s)
        got = roster.resolve(s, r.id, names=["Nguyen Anh Linh", "QUACH TRI DUNG"])
        assert {m["display_name"] for m in got["matched"]} == {"Linh Nguyen", "Kun"}


def test_a_name_two_people_share_is_ambiguous_not_guessed(db):
    """"Trang" is both Tabu (BUI THU TRANG) and Nhím (DINH HONG TRANG)."""
    with db.session() as s:
        r = _prod_room(s)
        got = roster.resolve(s, r.id, names=["Trang"])
        assert got["matched"] == []
        assert got["unresolved"] == []
        assert got["ambiguous"][0]["name"] == "Trang"
        assert {c["display_name"] for c in got["ambiguous"][0]["candidates"]} == {"Tabu", "Nhím"}

        # ...but the full name someone actually goes by is not ambiguous.
        exact = roster.resolve(s, r.id, names=["Bùi Trang"])
        assert [m["display_name"] for m in exact["matched"]] == ["Tabu"]


def test_a_name_nobody_has_is_still_unresolved(db):
    with db.session() as s:
        r = _prod_room(s)
        got = roster.resolve(s, r.id, names=["Thắng"])
        assert got["matched"] == []
        assert got["ambiguous"] == []
        assert got["unresolved"] == ["Thắng"]


def test_honorific_never_eats_the_whole_name(db):
    """A member whose name IS a kinship word must still resolve."""
    with db.session() as s:
        r = _make_room(s, "A", "a")
        s.add(Member(room_id=r.id, display_name="Cô", nickname="co", pin="1"))
        s.flush()
        assert roster.resolve(s, r.id, names=["Cô"])["matched"][0]["display_name"] == "Cô"


def test_teams_capture_helpers_are_removed():
    assert not hasattr(roster, "capture_sender")
    assert not hasattr(roster, "member_by_teams_id")
