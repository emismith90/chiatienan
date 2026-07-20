import json
import types

import pytest

import app.agent as agent_mod
from app.agent import (
    ToolInvocation,
    TurnResult,
    _unwrap_tool_args,
    _unwrap_tool_name,
    _unwrap_tool_result,
    run_turn,
)
from app.tools import ToolContext


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
