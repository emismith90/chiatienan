"""Unit tests for the Cursor-stream -> AG-UI translator (app.cursor_agui).

Uses fake message objects mirroring the real cursor_sdk shapes captured in the
PoC (assistant text-block deltas; mcp-wrapped tool calls with the MCP result
envelope). No network, no cursor_sdk needed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import app.cursor_agui as agui_mod
from app.cursor_agui import CursorAguiTranslator, cursor_run_to_agui, translate_messages


def _assistant(delta: str):
    return NS(type="assistant", message=NS(content=[NS(type="text", text=delta)]))


def _tool(call_id, status, tool_name, inner_args, result=None):
    """A custom/MCP tool call as Cursor emits it (wrapped under name='mcp')."""
    return NS(
        type="tool_call",
        call_id=call_id,
        name="mcp",
        status=status,
        args={"providerIdentifier": "custom-user-tools", "toolName": tool_name, "args": inner_args},
        result=result,
    )


def _types(events):
    return [e.type.value if hasattr(e.type, "value") else e.type for e in events]


def test_text_only_flow_wraps_in_start_content_end_and_run_events():
    events = translate_messages([_assistant("Hel"), _assistant("lo")], "t1", "r1")
    assert _types(events) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    # deltas preserved and share one message id
    contents = [e for e in events if e.type.value == "TEXT_MESSAGE_CONTENT"]
    assert [c.delta for c in contents] == ["Hel", "lo"]
    assert contents[0].message_id == contents[1].message_id


def test_tool_call_unwraps_name_args_and_result_and_splits_text_bubbles():
    msgs = [
        _assistant("Let me check."),
        _tool("call-1", "running", "run_sql_query", {"query": "SELECT 42 AS answer"}),
        _tool(
            "call-1",
            "completed",
            "run_sql_query",
            {"query": "SELECT 42 AS answer"},
            result={"status": "success", "value": {"content": [{"text": {"text": '{"answer":42}'}}]}},
        ),
        _assistant("The answer is 42."),
    ]
    events = translate_messages(msgs, "t1", "r1")
    assert _types(events) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",     # "Let me check."
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",       # tool call closes the first bubble
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START",     # post-tool answer = new bubble
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    start = next(e for e in events if e.type.value == "TOOL_CALL_START")
    assert start.tool_call_name == "run_sql_query"          # unwrapped from mcp
    args = next(e for e in events if e.type.value == "TOOL_CALL_ARGS")
    assert json.loads(args.delta) == {"query": "SELECT 42 AS answer"}  # inner args, not the envelope
    result = next(e for e in events if e.type.value == "TOOL_CALL_RESULT")
    assert result.content == '{"answer":42}'                # flattened MCP envelope
    assert start.tool_call_id == result.tool_call_id == "call-1"


def test_args_emitted_once_across_running_and_completed():
    msgs = [
        _tool("c1", "running", "t", {"a": 1}),
        _tool("c1", "completed", "t", {"a": 1}, result="ok"),
    ]
    events = translate_messages(msgs, "t", "r")
    assert _types(events).count("TOOL_CALL_START") == 1
    assert _types(events).count("TOOL_CALL_ARGS") == 1
    assert _types(events).count("TOOL_CALL_END") == 1
    assert _types(events).count("TOOL_CALL_RESULT") == 1


def test_status_error_emits_run_error_not_run_finished():
    events = translate_messages([_assistant("partial"), NS(type="status", status="ERROR", message="boom")], "t", "r")
    types = _types(events)
    assert "RUN_ERROR" in types
    assert "RUN_FINISHED" not in types
    # an open text bubble is closed before the error
    assert types.index("TEXT_MESSAGE_END") < types.index("RUN_ERROR")
    err = next(e for e in events if e.type.value == "RUN_ERROR")
    assert err.message == "boom"


def test_thinking_messages_are_dropped():
    events = translate_messages([NS(type="thinking", text="hmm")], "t", "r")
    assert _types(events) == ["RUN_STARTED", "RUN_FINISHED"]


def test_plain_string_result_passthrough():
    tr = CursorAguiTranslator("t", "r")
    out = tr.handle(_tool("c", "completed", "x", {}, result="raw string"))
    result = next(e for e in out if e.type.value == "TOOL_CALL_RESULT")
    assert result.content == "raw string"


# --------------------------------------------------------------------------- #
# Turn-level cap (Component A) — driver-level hard budget.
# --------------------------------------------------------------------------- #


class _FakeRun:
    """Minimal stand-in for a live cursor_sdk (Async)Run."""

    def __init__(self, msgs, *, supports_cancel=True):
        self._msgs = msgs
        self._supports_cancel = supports_cancel
        self.cancelled = False

    def supports(self, capability):
        return capability == "cancel" and self._supports_cancel

    def cancel(self):
        self.cancelled = True

    async def messages(self):
        for m in self._msgs:
            yield m


async def _drive(run, **kw):
    return [e async for e in cursor_run_to_agui(run, "t", "r", **kw)]


def _completed_tool(call_id):
    return _tool(call_id, "completed", "run_sql_query", {"q": call_id}, result="ok")


def _note_text(events):
    return "".join(
        e.delta for e in events if e.type.value == "TEXT_MESSAGE_CONTENT" and "⚠️" in (e.delta or "")
    )


async def test_cap_disabled_when_limits_zero_runs_to_completion():
    run = _FakeRun([_completed_tool(f"c{i}") for i in range(5)])
    events = await _drive(run, max_tools=0, max_seconds=0)
    assert not run.cancelled
    assert _types(events).count("TOOL_CALL_RESULT") == 5
    assert _types(events)[-1] == "RUN_FINISHED"
    assert _note_text(events) == ""


async def test_under_budget_no_cancel_no_note():
    run = _FakeRun([_completed_tool("c1"), _completed_tool("c2"), _assistant("done")])
    events = await _drive(run, max_tools=40, max_seconds=240)
    assert not run.cancelled
    assert "RUN_FINISHED" in _types(events) and "RUN_ERROR" not in _types(events)
    assert _note_text(events) == ""


async def test_tool_count_breach_cancels_notes_and_finishes():
    # max_tools=2 → cap fires after the 2nd completed tool; the 3rd must not be processed
    run = _FakeRun([_completed_tool("c1"), _completed_tool("c2"), _completed_tool("c3")])
    events = await _drive(run, max_tools=2, max_seconds=0)
    assert run.cancelled
    assert _types(events).count("TOOL_CALL_RESULT") == 2  # c3 dropped after the break
    assert "⚠️" in _note_text(events)
    types = _types(events)
    assert types[-1] == "RUN_FINISHED" and "RUN_ERROR" not in types


async def test_wall_clock_breach_cancels(monkeypatch):
    # monotonic: first call (start)=0, every subsequent call jumps past the limit.
    # Non-exhausting so it can't raise StopIteration if called during teardown.
    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 1000.0

    monkeypatch.setattr(agui_mod.time, "monotonic", fake_monotonic)
    run = _FakeRun([_completed_tool("c1"), _completed_tool("c2")])
    events = await _drive(run, max_tools=0, max_seconds=10)
    assert run.cancelled
    assert _types(events).count("TOOL_CALL_RESULT") == 1  # capped on the first message
    assert "⚠️" in _note_text(events)


async def test_cap_without_cancel_support_still_finishes_gracefully():
    run = _FakeRun([_completed_tool("c1"), _completed_tool("c2")], supports_cancel=False)
    events = await _drive(run, max_tools=1, max_seconds=0)
    assert not run.cancelled  # cancel() never called
    assert "⚠️" in _note_text(events)
    assert _types(events)[-1] == "RUN_FINISHED"
