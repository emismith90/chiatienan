"""A person the lookup missed must not vanish from the split.

Production, 2026-08-13, room "12B +2 🐔": *"nay ăn bún cá với anh Hưng chị Nhím
hết 175k"*. ``find_members`` matched Nhím, missed Hưng (his real name lives only
on his bank account, "Le Hoang Hung"), the model called him a guest in its prose
and then proposed the meal with ``guests: []``. The card billed two people for a
three-person bill and said nothing about the third. Two defences, both here:
the lookup now finds him, and ``propose_meal`` refuses the drop if it doesn't.
"""
from app.db import Database
from app.models import Member, Room
from app.tools import ToolContext, build_tools


def _room():
    d = Database("sqlite://")
    d.create_all()
    with d.session() as s:
        r = Room(name="12B", invite_token="t")
        s.add(r)
        s.flush()
        emi = Member(room_id=r.id, display_name="Emi", nickname="Emi", pin="1",
                     account_holder="Le Hoang Hung")
        nhim = Member(room_id=r.id, display_name="Nhím", nickname="Nhím", pin="2",
                      account_holder="DINH HONG TRANG")
        giang = Member(room_id=r.id, display_name="Giang Hoàng", nickname="Giang", pin="3",
                       account_holder="HOANG MINH GIANG")
        s.add_all([emi, nhim, giang])
        s.flush()
        ids = (r.id, emi.id, nhim.id, giang.id)
    return d, ids


def test_the_production_turn_now_resolves_everyone():
    d, (room_id, emi, nhim, giang) = _room()
    tools = build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=giang))

    found = tools["find_members"].execute({"names": ["anh Hưng", "chị Nhím"]})
    assert {m["id"] for m in found["matched"]} == {emi, nhim}
    assert found["unresolved"] == []

    draft = tools["propose_meal"].execute(
        {"participants": [giang, nhim, emi], "total": 175000, "dish": "bún cá"})
    assert draft["ok"] is True
    assert draft["per_head_preview"] == 58333


def test_proposal_is_refused_when_a_looked_up_name_is_left_out():
    d, (room_id, emi, nhim, giang) = _room()
    tools = build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=giang))

    missed = tools["find_members"].execute({"names": ["anh Thắng", "chị Nhím"]})
    assert missed["unresolved"] == ["anh Thắng"]

    out = tools["propose_meal"].execute({"participants": [giang, nhim], "total": 175000})
    assert out["ok"] is False
    assert "Thắng" in out["error"]
    assert "guests" in out["error"]


def test_naming_the_missing_person_as_a_guest_lets_the_proposal_through():
    d, (room_id, emi, nhim, giang) = _room()
    tools = build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=giang))
    tools["find_members"].execute({"names": ["anh Thắng"]})

    out = tools["propose_meal"].execute(
        {"participants": [giang, nhim], "total": 175000, "guests": ["Thắng"]})
    assert out["ok"] is True
    assert out["guests"] == ["Thắng"]
    assert out["per_head_preview"] == 58333  # three heads, two of them billed


def test_adding_the_person_as_a_member_also_clears_the_hole():
    d, (room_id, emi, nhim, giang) = _room()
    tools = build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=giang))
    tools["find_members"].execute({"names": ["Thắng"]})

    added = tools["add_member"].execute({"display_name": "Thắng", "nickname": "thang"})
    out = tools["propose_meal"].execute(
        {"participants": [giang, nhim, added["member_id"]], "total": 175000})
    assert out["ok"] is True


def test_finding_the_person_on_a_second_lookup_clears_the_hole():
    d, (room_id, emi, nhim, giang) = _room()
    tools = build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=giang))

    tools["find_members"].execute({"names": ["Hưng Lê"]})  # (matches via the bank name)
    tools["find_members"].execute({"names": ["Emi"]})

    out = tools["propose_meal"].execute({"participants": [giang, emi], "total": 175000})
    assert out["ok"] is True


def test_an_ambiguous_name_also_blocks_until_it_is_settled():
    d, (room_id, emi, nhim, giang) = _room()
    with d.session() as s:
        s.add(Member(room_id=room_id, display_name="Tabu", nickname="Tabu", pin="4",
                     account_holder="BUI THU TRANG"))
        s.flush()
    tools = build_tools(ToolContext(db=d, room_id=room_id, sender_member_id=giang))

    out = tools["find_members"].execute({"names": ["Trang"]})
    assert out["matched"] == []
    assert {c["display_name"] for c in out["ambiguous"][0]["candidates"]} == {"Nhím", "Tabu"}

    blocked = tools["propose_meal"].execute({"participants": [giang, emi], "total": 175000})
    assert blocked["ok"] is False
    assert "HỎI" in blocked["error"]  # ask which one, don't pick

    # Once the right Trang is in the split, the proposal goes through.
    ok = tools["propose_meal"].execute(
        {"participants": [giang, emi, nhim], "total": 175000})
    assert ok["ok"] is True
