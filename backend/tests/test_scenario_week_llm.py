"""Opt-in LLM eval: replays each scenario message through the real Cursor agent
and asserts the bot selected the expected tool(s). Skipped in CI — set
RUN_LLM_EVAL=1 (and valid Cursor creds) to run. Non-deterministic; tolerant of
extra tool calls (e.g. find_members) and VN/EN prose."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_LLM_EVAL"),
    reason="LLM eval is opt-in; set RUN_LLM_EVAL=1 to run.",
)

# message kind -> tool we expect the bot to call
EXPECTED_TOOL = {
    "meal_confirmed": "propose_meal",
    "leave_pending": "propose_meal",
    "payment": "record_payment",
    "add_member": "add_member",
    "settle": "settle_period",
    "settle_commit": "settle_period",
}


@pytest.mark.asyncio
async def test_scenario_week_llm(db):
    from app import drafts, ledger, tools
    from app.agent import run_turn
    from app.models import Member, Room
    from tests.golden.scenario_week import MEMBERS, STEPS

    ids = {}
    with db.session() as s:
        room = Room(name="WeekLLM", invite_token="week-llm")
        s.add(room); s.flush()
        for spec in MEMBERS:
            m = Member(room_id=room.id, display_name=spec["display_name"],
                       nickname=spec["nickname"], pin="1", **(spec.get("bank") or {}))
            s.add(m); s.flush()
            ids[spec["key"]] = m.id
        room_id = room.id

    for step in STEPS:
        if step["kind"] == "confirm_pending":
            with db.session() as s:  # confirmation is a UI action, not an LLM turn
                pass
            continue
        expected = EXPECTED_TOOL.get(step["kind"])
        if not expected or "message" not in step:
            continue
        ctx = tools.ToolContext(db=db, room_id=room_id, sender_member_id=ids[step["actor"]])
        result = await run_turn(step["message"], ctx)
        called = {inv.name for inv in result.tools}
        assert expected in called, f'{step["id"]}: expected {expected}, got {sorted(called)}'
