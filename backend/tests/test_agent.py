import json
import types
from types import SimpleNamespace

import pytest

import app.agent as agent_mod
from app import agui
from app.agent import (
    ToolInvocation,
    TurnResult,
    _render_prompt,
    _unwrap_tool_args,
    _unwrap_tool_name,
    _unwrap_tool_result,
    run_turn,
)
from app.prompt import build_system_prompt
from app.tools import ToolContext
from tests.test_ledger import _seed_room


# --- pure helpers ---------------------------------------------------------- #

def test_unwrap_tool_name_from_mcp_wrapper():
    assert _unwrap_tool_name("mcp", {"toolName": "record_meal"}) == "record_meal"
    assert _unwrap_tool_name("record_meal", {}) == "record_meal"
    assert _unwrap_tool_name(None, None) == "tool"


def test_unwrap_tool_args_from_wrapper():
    assert _unwrap_tool_args({"toolName": "x", "args": {"a": 1}}) == {"a": 1}
    assert _unwrap_tool_args({"a": 1}) == {"a": 1}


def test_unwrap_result_direct_dict():
    assert _unwrap_tool_result({"ok": True, "meal_id": 1}) == {"ok": True, "meal_id": 1}


def test_unwrap_result_from_mcp_envelope():
    payload = {"ok": True, "amount": 100}
    envelope = {"value": {"content": [{"text": {"text": json.dumps(payload)}}]}}
    assert _unwrap_tool_result(envelope) == payload


def test_turn_result_last_result_picks_last_ok():
    tr = TurnResult()
    tr.tools = [
        ToolInvocation("settle_period", {}, {"ok": False, "error": "x"}),
        ToolInvocation("settle_period", {}, {"ok": True, "transfers": []}),
    ]
    assert tr.last_result("settle_period") == {"ok": True, "transfers": []}
    assert tr.last_result("missing") is None


def test_system_prompt_mentions_payment_and_reset():
    p = build_system_prompt()
    assert "record_payment" in p
    assert "reset" in p.lower()


def test_render_prompt_baseline_unchanged():
    # No memory/history → identical to the pre-memory assembly.
    expected = f"{build_system_prompt(sender_name='An')}\n\n# Tin nhắn người dùng\nxin chào"
    assert _render_prompt("  xin chào  ", sender_name="An") == expected


def test_render_prompt_includes_sections_in_order():
    out = _render_prompt("ai trả", sender_name="An",
                         memory="- An hay trả", history="«An»: hôm qua 100k")
    assert "# Bộ nhớ dài hạn\n- An hay trả" in out
    assert "# Lịch sử hội thoại (gần đây)\n«An»: hôm qua 100k" in out
    # order: memory before history before the user message
    assert out.index("Bộ nhớ dài hạn") < out.index("Lịch sử hội thoại") < out.index("Tin nhắn người dùng")


# --- mocked run_turn ------------------------------------------------------- #

class _FakeAgent:
    def __init__(self, run):
        self._run = run

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, message, opts):
        return self._run


class _FakeAgents:
    def __init__(self, run):
        self._run = run

    async def create(self, options):
        return _FakeAgent(self._run)


class _FakeClient:
    def __init__(self, run):
        self.agents = _FakeAgents(run)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeRun:
    def __init__(self, messages):
        self._messages = messages

    async def messages(self):
        for m in self._messages:
            yield m

    def supports(self, _op):
        return False


def _text_msg(text):
    block = types.SimpleNamespace(type="text", text=text)
    return types.SimpleNamespace(type="assistant", message=types.SimpleNamespace(content=[block]))


def _tool_msg(tool_name, args, result):
    return types.SimpleNamespace(
        type="tool_call",
        status="completed",
        name="mcp",
        args={"toolName": tool_name, "args": args},
        result=result,
    )


@pytest.mark.asyncio
async def test_run_turn_collects_text_and_tool_results(monkeypatch, db):
    fake_run = _FakeRun([
        _tool_msg("settle_period", {"keyword": "since_last"}, {"ok": True, "transfers": [], "committed": False}),
        _text_msg("Mọi người đã cân bằng ✅"),
    ])

    monkeypatch.setattr(agent_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr(
        "app.cursor_runner.resolve_cursor_api_key", lambda *a, **k: "k", raising=False
    )
    monkeypatch.setattr(
        "app.cursor_runner.resolve_model_selection", lambda *a, **k: types.SimpleNamespace(id="composer-2.5", params=None), raising=False
    )

    async def _fake_launch(AsyncClient, workspace, local):
        return _FakeClient(fake_run)

    monkeypatch.setattr(agent_mod, "_launch_bridge_resilient", _fake_launch)

    ctx = ToolContext(db=db, room_id=1, sender_member_id=1, sender_name="An")
    result = await run_turn("ai trả tuần này", ctx)

    assert result.error is None
    assert "cân bằng" in result.final_text
    settle = result.last_result("settle_period")
    assert settle is not None and settle["transfers"] == []


# --- emit contract ---------------------------------------------------------- #

@pytest.mark.asyncio
async def test_emit_receives_events_for_messages():
    # Exercise the same loop shape run_turn uses: translate + await emit.
    seen = []
    async def emit(ev): seen.append(ev)
    msgs = [
        SimpleNamespace(type="assistant",
                        message=SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])),
        SimpleNamespace(type="tool_call", call_id="c1", name="propose_meal",
                        status="completed", args={"total": 1}, result={"ok": True}),
    ]
    turn_id = "t1"
    for ev in agui.start(turn_id):
        await emit(ev)
    for m in msgs:
        for ev in agui.translate(m, turn_id):
            await emit(ev)
    for ev in agui.finish(turn_id):
        await emit(ev)
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "agent.run.started" and kinds[-1] == "agent.run.finished"
    assert "agent.text.delta" in kinds and "agent.tool.result" in kinds


@pytest.mark.asyncio
async def test_run_turn_emits_finish_on_setup_failure(monkeypatch, db):
    """A setup-time failure (e.g. resolve_cursor_api_key raising because
    CURSOR_API_KEY is unset) must still reach agui.finish — otherwise a
    consumer sees agent.run.started with no terminal event and the timeline
    UI hangs forever."""

    def _boom(*a, **k):
        raise RuntimeError("CURSOR_API_KEY is not set")

    monkeypatch.setattr(agent_mod, "_ensure_workspace", lambda: "/tmp/chiatienan-test")
    monkeypatch.setattr(
        "app.cursor_runner.resolve_cursor_api_key", _boom, raising=False
    )

    room_id, member_ids = _seed_room(db, 1)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=member_ids[0], sender_name="An")

    seen = []

    async def emit(ev):
        seen.append(ev)

    result = await run_turn("ai trả tuần này", ctx, emit=emit)

    kinds = [e["type"] for e in seen]
    assert "agent.run.started" in kinds
    assert kinds[-1] in ("agent.run.finished", "agent.run.error")
    turn_ids = {e["turn_id"] for e in seen}
    assert len(turn_ids) == 1  # same turn_id on start and finish
    assert result.error is not None
