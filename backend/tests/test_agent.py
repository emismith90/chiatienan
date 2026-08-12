"""`run_turn` against a fake sidecar.

The contract under test is the frozen one: `run_turn(user_text, ctx, images, emit,
memory, history) -> TurnResult`. The fake feeds JSONL messages the way the real
bridge would, which is a far simpler stand-in than the old
`_FakeClient/_FakeAgents/_FakeAgent/_FakeRun` stack — because the sidecar now
returns a ready-made result instead of a stream to reassemble.
"""
import pytest

from app import agent
from app.tools import ToolContext


class FakeBridge:
    """Yields canned replies; records what Python sent back."""

    def __init__(self, script):
        self._script = script
        self.sent = []

    async def request(self, command):
        self.command = command
        for message in self._script:
            yield dict(message, req_id=command["req_id"])

    async def send(self, message):
        self.sent.append(message)


@pytest.fixture
def bridge(monkeypatch):
    def install(script):
        fake = FakeBridge(script)
        monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
        return fake
    return install


def _ctx(db):
    return ToolContext(db=db, room_id=1, sender_name="An")


async def test_a_plain_turn_hydrates_the_result(db, bridge):
    bridge([
        {"type": "agent.run.started", "turn_id": "t"},
        {"type": "turn_done", "final_text": "Đã ghi.", "tools": [], "error": None,
         "capped": False, "stats": {"tokens": 100}},
    ])
    result = await agent.run_turn("@bot ghi bữa trưa", _ctx(db))
    assert result.final_text == "Đã ghi."
    assert result.error is None and result.turn_id


async def test_agent_events_are_forwarded_untouched(db, bridge):
    # The sidecar emits the frontend's format; agui.py's translation is deleted.
    bridge([
        {"type": "agent.run.started", "turn_id": "t"},
        {"type": "agent.text.delta", "turn_id": "t", "delta": "Đã"},
        {"type": "agent.run.finished", "turn_id": "t"},
        {"type": "turn_done", "final_text": "Đã", "error": None},
    ])
    seen = []
    await agent.run_turn("x", _ctx(db), emit=lambda e: _collect(seen, e))
    assert [e["type"] for e in seen] == [
        "agent.run.started", "agent.text.delta", "agent.run.finished"]
    assert seen[1]["delta"] == "Đã"


async def _collect(sink, event):
    sink.append(event)


async def test_a_tool_call_round_trips_through_the_real_tool(db, bridge):
    from tests.test_ledger import _seed_room
    room_id, ids = _seed_room(db, 3)
    fake = bridge([
        {"type": "tool_call", "call_id": "c1", "name": "propose_meal",
         "args": {"payer": ids[0], "participants": ids, "total": 300000}},
        {"type": "turn_done", "final_text": "Đã ghi.", "error": None},
    ])
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=ids[0])
    result = await agent.run_turn("@bot 300k cả nhóm", ctx)

    # The tool executed in Python, over the real DB — no arithmetic crossed the wire.
    assert result.tools[0].name == "propose_meal"
    assert result.tools[0].result["type"] == "expense_draft"
    assert result.tools[0].result["per_head_preview"] == 100000
    # …and the result went back to the sidecar as a tool_result.
    assert fake.sent[0]["type"] == "tool_result"
    assert fake.sent[0]["call_id"] == "c1"
    assert "expense_draft" in fake.sent[0]["content"]


async def test_an_unknown_tool_is_an_ok_false_not_a_crash(db, bridge):
    fake = bridge([
        {"type": "tool_call", "call_id": "c", "name": "nope", "args": {}},
        {"type": "turn_done", "final_text": "?", "error": None},
    ])
    result = await agent.run_turn("x", _ctx(db))
    assert result.tools[0].result["ok"] is False
    assert fake.sent[0]["type"] == "tool_result"


async def test_a_tool_that_raises_becomes_ok_false_and_the_turn_continues(db, bridge, monkeypatch):
    def boom(_args):
        raise RuntimeError("db on fire")
    monkeypatch.setattr(agent, "build_tools",
                        lambda ctx: {"propose_meal": type("T", (), {"execute": staticmethod(boom)})()})
    bridge([
        {"type": "tool_call", "call_id": "c", "name": "propose_meal", "args": {}},
        {"type": "turn_done", "final_text": "Có lỗi.", "error": None},
    ])
    result = await agent.run_turn("x", _ctx(db))
    assert result.tools[0].result["ok"] is False
    assert "db on fire" in result.tools[0].result["error"]
    assert result.error is None            # the turn still produced a reply


async def test_a_capped_turn_is_not_an_error(db, bridge):
    # agent.py:374-384 always treated a cap as a partial answer; chat.py:558 posts
    # it as a normal reply. An error here would flip it to a ⚠️ in the room.
    bridge([{"type": "turn_done", "final_text": "một phần", "capped": True, "error": None}])
    result = await agent.run_turn("x", _ctx(db))
    assert result.error is None
    assert result.final_text == "một phần"


async def test_a_fatal_becomes_an_error_and_a_finish_event(db, bridge):
    bridge([{"type": "fatal", "message": "sidecar exited"}])
    seen = []
    result = await agent.run_turn("x", _ctx(db), emit=lambda e: _collect(seen, e))
    assert "sidecar exited" in result.error
    assert seen[-1]["type"] == "agent.run.error"


async def test_a_bridge_that_raises_still_returns_a_result(db, monkeypatch):
    class Exploding:
        async def request(self, command):
            raise RuntimeError("no node binary")
            yield  # pragma: no cover
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: Exploding())
    seen = []
    result = await agent.run_turn("x", _ctx(db), emit=lambda e: _collect(seen, e))
    assert "no node binary" in result.error
    assert seen and seen[-1]["type"] == "agent.run.error"


# --------------------------------------------------------------------------- #
# the command we build
# --------------------------------------------------------------------------- #

async def test_the_prompt_keeps_its_section_order(db, bridge):
    fake = bridge([{"type": "turn_done", "final_text": "", "error": None}])
    await agent.run_turn("cho tôi xem số dư", _ctx(db),
                         memory="- An trả 300k", history="An: chào", images=[{"data": "x"}])
    message = fake.command["message"]
    assert message.index("# Bộ nhớ dài hạn") < message.index("# Lịch sử hội thoại (gần đây)")
    assert message.index("# Lịch sử hội thoại (gần đây)") < message.index("# Ảnh kèm theo")
    assert message.index("# Ảnh kèm theo") < message.index("# Tin nhắn người dùng")


async def test_the_system_prompt_is_no_longer_prepended_to_the_message(db, bridge):
    # The sidecar has a real systemPromptOverride, so it travels as `system`.
    fake = bridge([{"type": "turn_done", "final_text": "", "error": None}])
    await agent.run_turn("xin chào", _ctx(db))
    assert fake.command["system"]
    assert fake.command["system"] not in fake.command["message"]
    assert fake.command["message"].startswith("# Tin nhắn người dùng")


async def test_the_image_count_is_announced_in_the_text(db, bridge):
    # Production attached the bill and the model still asked for the total in it.
    fake = bridge([{"type": "turn_done", "final_text": "", "error": None}])
    await agent.run_turn("ghi đi", _ctx(db), images=[{"data": "a"}, {"data": "b"}])
    assert "2 ảnh" in fake.command["message"]
    assert len(fake.command["images"]) == 2


async def test_the_command_carries_every_tool_skill_and_rules_file(db, bridge):
    fake = bridge([{"type": "turn_done", "final_text": "", "error": None}])
    await agent.run_turn("x", _ctx(db))
    assert len(fake.command["tools"]) == 14
    names = {s["name"] for s in fake.command["skills"]}
    assert {"record-meal", "record-payment", "balances", "pick-random"} <= names
    # every skill BODY ships, because the sidecar has no `read` tool
    assert all(s["body"].strip() for s in fake.command["skills"])
    assert any(f["path"] == "money-safety" for f in fake.command["context_files"])


async def test_the_command_carries_both_models_and_the_caps(db, bridge):
    fake = bridge([{"type": "turn_done", "final_text": "", "error": None}])
    await agent.run_turn("x", _ctx(db))
    assert fake.command["model"] and fake.command["vision_model"]
    assert fake.command["max_tools"] == 40 and fake.command["max_seconds"] == 120


def test_turn_result_helpers_still_filter_on_ok():
    r = agent.TurnResult(tools=[
        agent.ToolInvocation("propose_meal", {}, {"ok": False, "error": "x"}),
        agent.ToolInvocation("propose_meal", {}, {"ok": True, "n": 1}),
        agent.ToolInvocation("propose_meal", {}, {"ok": True, "n": 2}),
    ])
    assert r.last_result("propose_meal") == {"ok": True, "n": 2}
    assert [x["n"] for x in r.all_results("propose_meal")] == [1, 2]
    assert r.last_result("settle_period") is None
