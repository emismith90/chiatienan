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


def _seed_room(db, n, *, token="tok"):
    """Create a room with ``n`` members directly. Mirrors the helper in
    ``test_ledger.py``. Returns ``(room_id, [member_id, ...])``.
    """
    with db.session() as s:
        room = Room(name="Room", invite_token=token)
        s.add(room)
        s.flush()
        members = [
            Member(room_id=room.id, display_name=f"M{i}", nickname=f"m{i}", pin=str(i))
            for i in range(1, n + 1)
        ]
        s.add_all(members)
        s.flush()
        return room.id, [m.id for m in members]


def test_propose_meal_writes_nothing_and_previews(db):
    from app import ledger
    from app.models import Meal
    from app.tools import ToolContext, build_tools

    room_id, (a, b, c) = _seed_room(db, 3)   # reuse the helper pattern in this file
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)
    out = tools["propose_meal"].execute({
        "payer": a, "participants": [a, b, c], "total": 400_000, "guests": ["Emi"],
        "dish": "phở", "note": "test",
    })
    assert out["ok"] is True
    assert out["type"] == "expense_draft"
    assert out["member_participants"] == [a, b, c]
    assert out["guests"] == ["Emi"]
    assert out["bill_total"] == 400_000
    assert out["per_head_preview"] == 100_000
    assert out["dish"] == "phở"
    # nothing persisted
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_defaults_payer_to_sender(db):
    from app.tools import ToolContext, build_tools
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({"participants": [a, b], "total": 200_000})
    assert out["payer_member_id"] == a


def test_propose_meal_explicit_payer_overrides_sender(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({"payer": b, "participants": [a, b], "total": 200_000})
    assert out["ok"] is True
    assert out["payer_member_id"] == b
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_no_payer_and_no_sender_errors(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=None)
    out = build_tools(ctx)["propose_meal"].execute({"participants": [a, b], "total": 100_000})
    assert out["ok"] is False
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_invalid_participant_id_returns_error(db):
    """Fix 4: a non-int participant id must not raise uncaught out of the
    tool — it's a clarifying-question result like every other bad input."""
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({
        "participants": [a, "not-an-id"], "total": 100_000,
    })
    assert out["ok"] is False
    assert "error" in out
    with db.session() as s:
        assert s.query(Meal).count() == 0


def test_propose_meal_adjustments_round_trip(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    out = build_tools(ctx)["propose_meal"].execute({
        "participants": [a, b], "total": 300_000, "adjustments": [{"member": b, "amount": 50_000}],
    })
    assert out["ok"] is True
    assert out["adjustments"] == [{"member": b, "amount": 50_000}]
    with db.session() as s:
        assert s.query(Meal).count() == 0


def _seed_meal(d, room_id, payer, participants, total, **kwargs):
    """Write a meal straight through ``ledger.record_meal`` (the deterministic
    commit path), bypassing the (now write-nothing) ``propose_meal`` tool, so
    void/balances/settle tests still have a meal to work against.
    """
    with d.session() as s:
        res = ledger.record_meal(
            s,
            room_id=room_id,
            payer_member_id=payer,
            participants=participants,
            total_amount=total,
            source="web",
            logged_by=str(payer),
            **kwargs,
        )
        return res["meal_id"]


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
    meal_id = _seed_meal(d, room_id, an, [an, bi], 100000)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["void_meal"].execute({"meal_id": meal_id})
    assert out["ok"] is True and out["voided"] is True
    with d.session() as s:
        meal = s.get(Meal, meal_id)
        assert meal.voided is True
        assert meal.voided_by == str(an)


def test_void_meal_tool_cannot_void_another_rooms_meal():
    d, (room_id, an, bi) = _ctx()
    other_room_id, other_id = _other_room_member(d)
    meal_id = _seed_meal(d, room_id, an, [an, bi], 100000)

    other_ctx = ToolContext(db=d, room_id=other_room_id, sender_member_id=other_id)
    other_tools = build_tools(other_ctx)
    out = other_tools["void_meal"].execute({"meal_id": meal_id})
    assert out["ok"] is False and "error" in out


def test_get_period_balances_tool_scoped_to_room():
    d, (room_id, an, bi) = _ctx()
    other_room_id, other_id = _other_room_member(d)
    _seed_meal(d, room_id, an, [an, bi], 100000)

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


def test_update_member_tool_edits_renames_and_handles_errors(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)

    ok = tools["update_member"].execute({"target": "m2", "display_name": "Bob", "nickname": "bob"})
    assert ok["ok"] is True and ok["nickname"] == "bob" and ok["display_name"] == "Bob"
    with db.session() as s:
        assert s.get(Member, b).display_name == "Bob"

    # rename onto an existing nickname is rejected
    clash = tools["update_member"].execute({"target": "bob", "nickname": "m1"})
    assert clash["ok"] is False and "error" in clash
    # unknown target
    missing = tools["update_member"].execute({"target": "nobody"})
    assert missing["ok"] is False and "error" in missing


def test_delete_member_tool_soft_deletes_out_of_roster_but_reversible(db):
    room_id, (a, b) = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)

    out = tools["delete_member"].execute({"target": b})
    assert out["ok"] is True and out["member_id"] == b
    with db.session() as s:
        assert s.get(Member, b).active is False
    # removed from selection/roster
    assert {m["id"] for m in tools["find_members"].execute({"all_active": True})["matched"]} == {a}
    # restore via update_member(active=True)
    tools["update_member"].execute({"target": b, "active": True})
    assert {m["id"] for m in tools["find_members"].execute({"all_active": True})["matched"]} == {a, b}


def test_settle_still_names_a_deleted_member(db):
    room_id, (a, b) = _seed_room(db, 2)
    _seed_meal(db, room_id, a, [a, b], 100_000)  # b owes a 50k
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=a, sender_name="M1")
    tools = build_tools(ctx)

    tools["delete_member"].execute({"target": b})  # b removed after incurring a debt
    out = tools["settle_period"].execute({"keyword": "since_last"})
    assert out["ok"] is True
    names = {row["from_name"] for row in out["transfers"]} | {row["to_name"] for row in out["transfers"]}
    assert "?" not in names            # deleted member's name still resolves
    assert "M2" in names               # b's display name is present


def test_settle_period_tool_commit_uses_sender_as_requested_by():
    d, (room_id, an, bi) = _ctx()
    _seed_meal(d, room_id, an, [an, bi], 100000)

    ctx = ToolContext(db=d, room_id=room_id, sender_member_id=an, sender_name="An")
    tools = build_tools(ctx)
    out = tools["settle_period"].execute({"keyword": "since_last", "commit": True})
    assert out["ok"] is True and out["committed"] is True

    with d.session() as s:
        settlement = ledger.last_settlement(s, room_id)
        assert settlement is not None
        assert settlement.requested_by == str(an)
