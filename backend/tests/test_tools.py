from app import ledger
from app.db import Database
from app.models import Meal, Room, Member
from app.tools import ToolContext, build_tools


def _ctx():
    d = Database("sqlite://")
    d.create_all()
    with d.session() as s:
        r = Room(name="A", invite_token="t")
        s.add(r)
        s.flush()
        an = Member(room_id=r.id, display_name="An", nickname="an", pin="1")
        bi = Member(room_id=r.id, display_name="Bình", nickname="binh", pin="2")
        s.add_all([an, bi])
        s.flush()
        ids = (r.id, an.id, bi.id)
    return d, ids


def _other_room_member(d):
    with d.session() as s:
        r2 = Room(name="B", invite_token="t2")
        s.add(r2)
        s.flush()
        other = Member(room_id=r2.id, display_name="Khác", nickname="khac", pin="9")
        s.add(other)
        s.flush()
        return r2.id, other.id


def test_record_meal_tool_scopes_to_room_and_sender():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["record_meal"].execute({"participants": [an, bi], "total": 100000})
    assert out["ok"] and out["payer"]["id"] == an
    assert {sh["id"]: sh["amount"] for sh in out["shares"]} == {an: 50000, bi: 50000}


def test_record_meal_tool_writes_source_web_and_logged_by_sender():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["record_meal"].execute({"participants": [an, bi], "total": 100000})
    with d.session() as s:
        meal = s.get(Meal, out["meal_id"])
        assert meal.source == "web"
        assert meal.logged_by == str(an)


def test_record_meal_tool_explicit_payer_overrides_sender_but_logged_by_stays_sender():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["record_meal"].execute({"payer": bi, "participants": [an, bi], "total": 100000})
    assert out["ok"] and out["payer"]["id"] == bi
    with d.session() as s:
        meal = s.get(Meal, out["meal_id"])
        assert meal.logged_by == str(an)


def test_record_meal_tool_rejects_participant_from_another_room():
    d, (room_id, an, bi) = _ctx()
    _, outsider_id = _other_room_member(d)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["record_meal"].execute({"participants": [an, outsider_id], "total": 100000})
    assert out["ok"] is False and "error" in out


def test_record_meal_tool_no_payer_and_no_sender_errors():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=None)
    tools = build_tools(ctx)
    out = tools["record_meal"].execute({"participants": [an, bi], "total": 100000})
    assert out["ok"] is False


def test_find_members_tool_matches_names_and_all_active():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an)
    tools = build_tools(ctx)

    out = tools["find_members"].execute({"names": ["An"]})
    assert out["ok"] is True
    assert {m["id"] for m in out["matched"]} == {an}
    assert out["unresolved"] == []

    out_all = tools["find_members"].execute({"all_active": True})
    assert {m["id"] for m in out_all["matched"]} == {an, bi}


def test_find_members_tool_does_not_see_other_rooms_members():
    d, (room_id, an, bi) = _ctx()
    _other_room_member(d)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an)
    tools = build_tools(ctx)
    out = tools["find_members"].execute({"all_active": True})
    assert {m["id"] for m in out["matched"]} == {an, bi}


def test_find_members_tool_schema_has_no_include_tagged():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an)
    tools = build_tools(ctx)
    assert "include_tagged" not in tools["find_members"].input_schema["properties"]


def test_void_meal_tool_marks_voided_with_sender_as_by():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    recorded = tools["record_meal"].execute({"participants": [an, bi], "total": 100000})

    out = tools["void_meal"].execute({"meal_id": recorded["meal_id"]})
    assert out["ok"] is True and out["voided"] is True
    with d.session() as s:
        meal = s.get(Meal, recorded["meal_id"])
        assert meal.voided is True
        assert meal.voided_by == str(an)


def test_void_meal_tool_cannot_void_another_rooms_meal():
    d, (room_id, an, bi) = _ctx()
    other_room_id, other_id = _other_room_member(d)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    recorded = tools["record_meal"].execute({"participants": [an, bi], "total": 100000})

    other_ctx = ToolContext(db=d, room_id=other_room_id, sender_member_id=other_id)
    other_tools = build_tools(other_ctx)
    out = other_tools["void_meal"].execute({"meal_id": recorded["meal_id"]})
    assert out["ok"] is False and "error" in out


def test_get_period_balances_tool_scoped_to_room():
    d, (room_id, an, bi) = _ctx()
    other_room_id, other_id = _other_room_member(d)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    tools["record_meal"].execute({"participants": [an, bi], "total": 100000})

    other_ctx = ToolContext(db=d, room_id=other_room_id, sender_member_id=other_id)
    other_tools = build_tools(other_ctx)
    out = other_tools["get_period_balances"].execute({"to": "2999-01-01"})
    assert out["ok"] is True
    assert out["balances"] == []


def test_add_member_tool_creates_unclaimed_member_and_rejects_duplicate_nickname():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)

    out = tools["add_member"].execute({"display_name": "Chi", "nickname": "chi"})
    assert out["ok"] is True
    assert out["nickname"] == "chi"
    with d.session() as s:
        m = s.get(Member, out["member_id"])
        assert m.room_id == room_id
        assert m.pin is None
        assert m.display_name == "Chi"

    dup = tools["add_member"].execute({"display_name": "Chi2", "nickname": "chi"})
    assert dup["ok"] is False and "error" in dup


def test_settle_period_tool_commit_uses_sender_as_requested_by():
    d, (room_id, an, bi) = _ctx()
    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    tools["record_meal"].execute({"participants": [an, bi], "total": 100000, "payer": an})

    out = tools["settle_period"].execute({"keyword": "since_last", "commit": True})
    assert out["ok"] is True and out["committed"] is True

    with d.session() as s:
        settlement = ledger.last_settlement(s, room_id)
        assert settlement is not None
        assert settlement.requested_by == str(an)
