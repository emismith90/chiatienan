"""The kernos seam into ``run_turn`` (plan Task 1.4, review finding 1).

Two claims: with nothing on the context, the command the bridge receives is what
``default_engine_spec`` + today's renderers build; with a spec and overrides on
the context, *those* reach the bridge — while the six-argument signature and the
test fakes stay untouched.
"""
from dataclasses import replace

from app import agent
from app.prompt import build_system_prompt
from app.tools import ToolContext
from kernos.engine import EngineSpec
from tests.test_agent import FakeBridge

_DONE = [{"type": "turn_done", "final_text": "ok", "tools": [], "error": None, "capped": False, "stats": None}]


def _install(monkeypatch):
    fake = FakeBridge(_DONE)
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
    return fake


async def test_default_path_builds_todays_command(db, monkeypatch):
    fake = _install(monkeypatch)
    ctx = ToolContext(db=db, room_id=1, sender_member_id=7, sender_name="An")
    await agent.run_turn("hi", ctx, memory="M", history="H")
    cmd = fake.command
    expected = replace(agent.default_engine_spec(),
                       system=build_system_prompt(sender_name="An", sender_id=7)).to_run_command(
        req_id=cmd["req_id"], turn_id=cmd["turn_id"],
        message=agent._render_prompt("hi", sender_name="An", memory="M", history="H"),
        images=None, tools=agent.tool_manifest())
    assert cmd == expected
    assert cmd["req_id"] == f"run-{cmd['turn_id']}"


async def test_a_spec_on_the_context_reaches_the_bridge(db, monkeypatch):
    fake = _install(monkeypatch)
    spec = EngineSpec(model="other/model", vision_model="other/vision", thinking="low",
                      builtin_tools=[], max_tools=3, max_seconds=9, cwd="/x", agent_dir="/y",
                      skills=[{"name": "k", "description": "", "body": "B"}], context_files=[],
                      settings={"compaction": {"enabled": False}})
    ctx = ToolContext(db=db, room_id=1, sender_name="An", engine_spec=spec,
                      system_override="SYSTEM", message_override="MESSAGE")
    result = await agent.run_turn("ignored", ctx, memory="ignored too")
    cmd = fake.command
    assert (cmd["model"], cmd["vision_model"], cmd["thinking"]) == ("other/model", "other/vision", "low")
    assert cmd["builtin_tools"] == [] and cmd["max_tools"] == 3 and cmd["max_seconds"] == 9
    assert cmd["system"] == "SYSTEM" and cmd["message"] == "MESSAGE"
    assert cmd["skills"] == [{"name": "k", "description": "", "body": "B"}]
    assert cmd["settings"] == {"compaction": {"enabled": False}}
    assert cmd["tools"] == agent.tool_manifest()          # tools stay code-owned
    assert result.final_text == "ok"


async def test_a_spec_without_overrides_still_renders_system_and_message_per_turn(db, monkeypatch):
    fake = _install(monkeypatch)
    spec = replace(agent.default_engine_spec(), model="pinned/model")
    ctx = ToolContext(db=db, room_id=1, sender_member_id=2, sender_name="Bình", engine_spec=spec)
    await agent.run_turn("xin chào", ctx)
    assert fake.command["model"] == "pinned/model"
    assert fake.command["system"] == build_system_prompt(sender_name="Bình", sender_id=2)
    assert fake.command["message"].endswith("# Tin nhắn người dùng\nxin chào")


async def test_the_context_defaults_leave_the_seam_closed(db):
    ctx = ToolContext(db=db, room_id=1)
    assert ctx.engine_spec is None and ctx.system_override is None and ctx.message_override is None
