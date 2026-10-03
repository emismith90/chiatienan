"""The agent changes places only by proposing; a person confirms on a card, and
`places.py`'s own rules check and apply it (generic tools + confirm by default)."""
import pytest

import app.agent as agent_mod
from app import chat, drafts, places, store
from app.agent import ToolInvocation, TurnResult
from app.db import Database
from app.models import LegacyPlace, Room
from app.tools import ToolContext, build_tools
from kernos.data import actions
from tests.places_util import add_place, place_by_slug, set_place


@pytest.fixture()
def world():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.flush()
        add_place(s, room_id=1, slug="pho-vui", name="Phở Vui", aliases=["vui"], address="1 Hàng Bông")
        add_place(s, room_id=1, slug="bun-bo-hue-1992", name="Bún bò Huế 1992")
    return db, build_tools(ToolContext(db=db, room_id=1))


def _confirm(db, action):
    with db.session() as s:
        return actions.apply(store.DATA, action, actor="member:1", session=s)


def test_create_proposes_then_confirming_adds_it_above_the_legacy_ids(world):
    db, tools = world
    with db.session() as s:
        s.add(LegacyPlace(id=150, room_id=1, slug="old", name="Old", walkable=True, active=True,
                          created_at=places.datetime(2026, 1, 1)))
    prop = tools["places_create"].execute({"data": {"name": "Bánh mì Phượng", "aliases": ["banh mi phuong"],
                                                    "address": "20 Hàng Bún"}})
    assert prop["ok"] and prop["type"] == "record_action" and "NOT saved" in prop["note"]
    assert prop["action"]["after"]["slug"] == "banh-mi-phuong" and "id" not in prop["action"]["after"]
    with db.session() as s:
        assert all(p.slug != "banh-mi-phuong" for p in places.list_places(s, 1))       # nothing yet
    done = _confirm(db, prop["action"])
    with db.session() as s:
        p = place_by_slug(s, 1, "banh-mi-phuong")
    assert int(done["doc_id"]) == p.id and p.id > 150 and p.address == "20 Hàng Bún" and p.walkable


def test_create_refuses_a_name_already_on_the_list_and_says_how_to_bring_back_a_hidden_one(world):
    db, tools = world
    dup = tools["places_create"].execute({"data": {"name": "Phở  Vui"}})
    assert dup["ok"] is False and "already on the list" in dup["error"]
    with db.session() as s:
        set_place(s, place_by_slug(s, 1, "pho-vui").id, active=False)
    hidden = tools["places_create"].execute({"data": {"name": "Phở Vui"}})
    assert "active=true" in hidden["error"]
    assert tools["places_create"].execute({"data": {}})["ok"] is False
    assert tools["places_create"].execute({"data": {"name": "X", "slug": "x"}})["ok"] is False  # not settable


def test_update_changes_only_the_given_fields_and_never_the_slug(world):
    db, tools = world
    with db.session() as s:
        pid = place_by_slug(s, 1, "pho-vui").id
    prop = tools["places_update"].execute({"doc_id": str(pid), "changes": {"phone": "0901", "name": "Phở Vui (Hàng Bông)"}})
    assert prop["ok"]
    assert {c["field"] for c in actions.changes(prop["action"])} == {"phone", "name"}
    _confirm(db, prop["action"])
    with db.session() as s:
        p = places.get_place(s, 1, pid)
    assert (p.slug, p.name, p.phone, p.aliases, p.address) == ("pho-vui", "Phở Vui (Hàng Bông)", "0901", ["vui"], "1 Hàng Bông")
    assert tools["places_update"].execute({"doc_id": str(pid), "changes": {"slug": "x"}})["ok"] is False
    assert tools["places_update"].execute({"doc_id": str(pid), "changes": {"phone": "0901"}})["ok"] is False  # no change
    assert tools["places_update"].execute({"doc_id": "999", "changes": {"phone": "1"}})["ok"] is False


def test_delete_hides_it_from_find_suggest_and_search_but_keeps_the_record(world):
    db, tools = world
    with db.session() as s:
        pid = place_by_slug(s, 1, "bun-bo-hue-1992").id
    prop = tools["places_delete"].execute({"doc_id": str(pid)})
    assert prop["ok"] and prop["action"]["soft"] and actions.headline(prop["action"]) == "Hide place «Bún bò Huế 1992»"
    assert "Bún bò Huế 1992" in [p["name"] for p in tools["find_places"].execute({"all": True})["places"]]
    _confirm(db, prop["action"])
    names = [p["name"] for p in tools["find_places"].execute({"all": True})["places"]]
    assert names == ["Phở Vui"]
    assert all("1992" not in d["data"]["name"] for d in tools["places_search"].execute({"query": "bún bò"})["documents"])
    with db.session() as s:
        assert places.get_place(s, 1, pid).active is False                               # still there
    assert "already hidden" in tools["places_delete"].execute({"doc_id": str(pid)})["error"]


def test_a_card_is_refused_when_the_panel_changed_the_place_meanwhile(world):
    db, tools = world
    with db.session() as s:
        pid = place_by_slug(s, 1, "pho-vui").id
    prop = tools["places_update"].execute({"doc_id": str(pid), "changes": {"phone": "0901"}})
    with db.session() as s:
        places.edit_place(s, places.get_place(s, 1, pid), {"phone": "0999"})        # someone, in the panel
    with pytest.raises(actions.ActionRefused, match="changed since"):
        _confirm(db, prop["action"])
    with db.session() as s:
        assert places.get_place(s, 1, pid).phone == "0999"


async def test_a_turn_ends_on_the_card_and_confirming_it_applies_every_change(db, monkeypatch):
    from tests.test_ledger import _seed_room
    room_id, m = _seed_room(db, 2)
    with db.session() as s:
        add_place(s, room_id=room_id, slug="pho-ga", name="Phở gà")
        pid = place_by_slug(s, room_id, "pho-ga").id

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        tools = build_tools(ctx)
        a1, a2 = {"doc_id": str(pid)}, {"data": {"name": "Cơm rang Tuấn"}}
        return TurnResult(final_text="Bấm Xác nhận để ẩn Phở gà và thêm Cơm rang Tuấn.", turn_id="t-p", tools=[
            ToolInvocation("places_delete", a1, tools["places_delete"].execute(a1)),
            ToolInvocation("places_create", a2, tools["places_create"].execute(a2))])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    card = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix xoá phở gà, thêm cơm rang tuấn")
    assert card.kind == "record_draft" and "Hide place «Phở gà»" in card.body and "Add place «Cơm rang Tuấn»" in card.body
    with db.session() as s:
        assert places.get_place(s, room_id, pid).active                                  # nothing yet
        drafts.commit_any(s, card.id, room_id, logged_by=str(m[1]))
    with db.session() as s:
        live = [p.name for p in places.list_places(s, room_id)]
    assert live == ["Cơm rang Tuấn"]
