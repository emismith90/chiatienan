from app import roster


def test_create_and_list_members(db):
    with db.session() as s:
        roster.create_member(s, display_name="An", aliases=["Anh"])
        roster.create_member(s, display_name="Bình", active=False)
        active = roster.list_members(s, active_only=True)
        assert [m.display_name for m in active] == ["An"]
        assert len(roster.list_members(s)) == 2


def test_resolve_by_name_and_alias(db):
    with db.session() as s:
        an = roster.create_member(s, display_name="An", aliases=["Anh Hai"])
        res = roster.resolve(s, names=["an", "Anh Hai", "Unknown"])
        ids = {m["id"] for m in res["matched"]}
        assert an.id in ids
        assert res["unresolved"] == ["Unknown"]


def test_resolve_all_active(db):
    with db.session() as s:
        roster.create_member(s, display_name="An")
        roster.create_member(s, display_name="Bình")
        roster.create_member(s, display_name="Cũ", active=False)
        res = roster.resolve(s, all_active=True)
        names = {m["display_name"] for m in res["matched"]}
        assert names == {"An", "Bình"}


def test_resolve_by_mention_teams_id(db):
    with db.session() as s:
        an = roster.create_member(s, display_name="An", teams_user_id="29:aaa")
        res = roster.resolve(s, mentions=[{"teams_user_id": "29:aaa", "name": "An"}])
        assert res["matched"][0]["id"] == an.id
        # unknown mention is unresolved
        res2 = roster.resolve(s, mentions=[{"teams_user_id": "29:zzz", "name": "Ghost"}])
        assert res2["matched"] == [] and res2["unresolved"] == ["Ghost"]


def test_capture_sender_creates_inactive_stub(db):
    with db.session() as s:
        m = roster.capture_sender(s, teams_user_id="29:new", aad_object_id="aad-1", name="New Guy")
        assert m.active is False
        assert m.teams_user_id == "29:new"
    with db.session() as s:
        # second capture returns the same member, doesn't duplicate
        again = roster.capture_sender(s, teams_user_id="29:new", aad_object_id=None, name="New Guy")
        assert len(roster.list_members(s)) == 1
        assert again.teams_user_id == "29:new"
