"""kernos.data.actions: collection options, server ids, soft delete, and which proposals
become the card."""
import pytest

from app.agent import ToolInvocation, TurnResult
from app.kernel import kernel_for
from app.tools import ToolContext, build_tools
from kernos.content.errors import Invalid
from kernos.data import actions
from tests.test_ledger import _seed_room

TODO = {"type": "object", "required": ["id", "text"],
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "open": {"type": "boolean"}}}


@pytest.mark.parametrize("options, needle", [
    ({"nope": 1}, "unknown"), ({"confirm": "yes"}, "confirm"), ({"soft_delete": "text"}, "boolean"),
    ({"ids": "uuid"}, "ids"), ({"editable": ["id"]}, "editable"), ({"agent_tools": ["upsert"]}, "agent_tools")])
def test_bad_options_are_refused(options, needle):
    with pytest.raises(Invalid, match=needle):
        actions.check_options(options, schema=TODO, key="id", mode="table")


def test_options_are_stored_as_given_so_a_declaration_compares_equal():
    assert actions.check_options(None, schema=TODO, key="id", mode="table") is None
    assert actions.check_options({"soft_delete": "open"}, schema=TODO, key="id", mode="table") == {"soft_delete": "open"}


def _todo(db, options):
    room_id, m = _seed_room(db, 2)
    k = kernel_for(db)
    bid = k.seed_report["business_id"]
    k.data.put_collection(bid, "todo", name="Todos", schema=TODO, key="id", actor="admin",
                          reserved=k.reserved_tool_names(), options=options)
    tools = build_tools(ToolContext(db=db, room_id=room_id, sender_member_id=m[0],
                                    tool_config={"packs": [{"pack": "collections"}]}))
    return room_id, k, k.data.get_collection(bid, "todo"), tools


def test_server_ids_are_drawn_when_applied_and_soft_delete_hides(db):
    room_id, k, col, tools = _todo(db, {"ids": "server", "soft_delete": "open"})
    assert "id" not in tools["todo_create"].input_schema["properties"]["data"]["properties"]
    assert "Hide" in tools["todo_delete"].description
    p1 = tools["todo_create"].execute({"data": {"text": "mua bia", "open": True}})
    p2 = tools["todo_create"].execute({"data": {"text": "đặt bàn", "open": True}})
    with db.session() as s:
        ids = [actions.apply(k.data, p["action"], actor="member:1", session=s)["doc_id"] for p in (p1, p2)]
    assert ids == ["1", "2"]
    gone = tools["todo_delete"].execute({"doc_id": "1"})
    assert gone["action"]["after"]["open"] is False
    with db.session() as s:
        actions.apply(k.data, gone["action"], actor="member:1", session=s)
    assert [d["doc_id"] for d in tools["todo_find"].execute({})["documents"]] == ["2"]
    assert k.data.get_document(col, room_id, "1")["data"]["open"] is False             # kept, hidden


def test_only_this_agents_own_proposals_become_the_card(db):
    room_id, k, col, tools = _todo(db, None)
    mine = tools["todo_create"].execute({"data": {"id": "a", "text": "x"}})
    sub = tools["todo_create"].execute({"data": {"id": "b", "text": "y"}})
    result = TurnResult(tools=[ToolInvocation("todo_create", {}, mine), ToolInvocation("todo_create", {}, mine),
                               ToolInvocation("todo_create", {}, sub, from_agent="reviewer")])
    assert [a["after"]["id"] for a in actions.proposed(result)] == ["a"]          # once, and not the sub's
    draft = actions.render(result)
    assert draft.kind == "record_draft" and len(draft.payload["actions"]) == 1
    assert actions.render(TurnResult()) is None
