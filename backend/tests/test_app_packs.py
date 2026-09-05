"""The lunch packs (plan Tasks 3.1, 3.3): partition, legacy order, per-tool content."""
import pytest

import app.agent as agent_mod
from app import chat
from app.agent import TurnResult
from app.kernel import kernel_for
from app.packs import LEGACY_ORDER, MEMBER_TOOLS, MONEY_TOOLS, PLACES_TOOLS, LunchPlacesPack, RoomMembersPack, lunch_ledger_pack
from app.tools import ToolContext, _legacy_build_tools, build_tools, tool_manifest
from kernos.content import ToolPackRef
from kernos.packs import PackError
from tests.test_ledger import _seed_room


def test_the_three_packs_partition_the_legacy_tools_in_legacy_order(db):
    room_id, m = _seed_room(db, 2)
    ctx = ToolContext(db=db, room_id=room_id)
    legacy = _legacy_build_tools(ctx)
    assert tuple(legacy) == LEGACY_ORDER and len(LEGACY_ORDER) == 19
    money, places, members = lunch_ledger_pack().tools(ctx), LunchPlacesPack().tools(ctx), RoomMembersPack().tools(ctx)
    assert set(money) == MONEY_TOOLS and set(places) == PLACES_TOOLS and set(members) == MEMBER_TOOLS
    assert not (MONEY_TOOLS & PLACES_TOOLS) and not (MONEY_TOOLS & MEMBER_TOOLS) and not (PLACES_TOOLS & MEMBER_TOOLS)
    assert MONEY_TOOLS | PLACES_TOOLS | MEMBER_TOOLS == set(LEGACY_ORDER)
    # composed through the seam with all packs on: same names, same order, same schemas
    kernel_for(db)
    ctx.tool_config = {"packs": [{"pack": "lunch_ledger"}, {"pack": "room_members"}, {"pack": "lunch_places"}]}
    composed = build_tools(ctx)
    assert list(composed) == list(legacy)
    assert [(n, t.description, t.input_schema) for n, t in composed.items()] == \
        [(n, t.description, t.input_schema) for n, t in legacy.items()]
    assert tool_manifest(ctx) == tool_manifest()


def test_disabling_and_redescribing_a_tool_is_content(db):
    room_id, m = _seed_room(db, 2)
    kernel_for(db)
    ctx = ToolContext(db=db, room_id=room_id, tool_config={"packs": [
        {"pack": "lunch_ledger", "tools": {"pick_random": {"enabled": False},
                                          "settle_period": {"description": "Who pays whom."}}}]})
    tools = build_tools(ctx)
    assert "pick_random" not in tools and "find_places" not in tools          # places pack not enabled
    assert tools["settle_period"].description == "Who pays whom."
    assert [t["name"] for t in tool_manifest(ctx)] == [n for n in LEGACY_ORDER if n in MONEY_TOOLS and n != "pick_random"]
    with pytest.raises(PackError):
        build_tools(ToolContext(db=db, room_id=room_id, tool_config={"packs": [{"pack": "lunch_ledger", "tools": {"zzz": {}}}]}))


async def test_a_bound_profile_without_pick_random_never_offers_it_to_the_engine(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    k = kernel_for(db)
    pid = k.seed_report["profile_id"]
    d = k.store.create_draft(pid, actor="admin")
    k.store.update_draft(d["id"], {"tool_packs": [{"pack": "lunch_ledger", "tools": {"pick_random": {"enabled": False}}},
                                                  {"pack": "room_members"}, {"pack": "lunch_places"}]}, actor="admin")
    k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="test")
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["names"] = [t["name"] for t in tool_manifest(ctx)]
        seen["refused"] = ctx.tool_config is not None and "pick_random" not in build_tools(ctx)
        return TurnResult(final_text="ok")

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix bốc thăm")
    assert "pick_random" not in seen["names"] and "find_places" in seen["names"] and seen["refused"]
