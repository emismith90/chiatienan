"""AG-UI events from a turn (design §12.4; Phase 9 review F3)."""
import json

from kernos.api.agui import AguiEventSink, from_legacy
from kernos.kernel.events import EVENT_TYPES, MESSAGE_REPUBLISHED, TurnEvent, to_legacy


def _sink():
    written = []

    async def write(e):
        written.append(e)
    ids = iter(f"id{i}" for i in range(1, 100))
    return AguiEventSink(write, thread_id="room-1", ids=lambda: next(ids)), written


def _kinds(events):
    return [e["type"] for e in events]


async def test_a_sidecar_stream_becomes_an_ordered_agui_run():
    sink, out = _sink()
    for e in [{"type": "agent.run.started", "turn_id": "t1"},
              {"type": "agent.text.delta", "turn_id": "t1", "delta": "Để "},
              {"type": "agent.text.delta", "turn_id": "t1", "delta": "xem"},
              {"type": "agent.tool.start", "turn_id": "t1", "call_id": "c1", "name": "settle_period", "args": {"keyword": "since_last"}},
              {"type": "agent.tool.result", "turn_id": "t1", "call_id": "c1", "name": "settle_period", "status": "completed", "result": {"ok": True}},
              {"type": "agent.text.delta", "turn_id": "t1", "delta": "Xong."},
              {"type": "agent.run.finished", "turn_id": "t1"},
              {"type": "agent.validation.warned", "turn_id": "t1", "reason": "unbacked amounts [5000]"},
              {"type": "message", "id": 7, "kind": "payment_draft"}]:
        await sink.emit_raw(e)
    await sink.finish()
    assert _kinds(out) == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT",
                           "TEXT_MESSAGE_END", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT",
                           "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "CUSTOM", "RUN_FINISHED"]
    assert out[0]["threadId"] == "room-1" and out[0]["runId"] == "t1" and out[-1]["runId"] == "t1"
    assert out[1]["messageId"] == "id1" and out[1]["role"] == "assistant" and out[4]["messageId"] == "id1"
    assert out[5] == {**out[5], "toolCallId": "c1", "toolCallName": "settle_period", "parentMessageId": "id1"}
    assert json.loads(out[6]["delta"]) == {"keyword": "since_last"} and out[7]["toolCallId"] == "c1"
    assert out[8]["role"] == "tool" and json.loads(out[8]["content"]) == {"ok": True} and out[8]["messageId"] == "id2"
    assert out[9]["messageId"] == "id3"                                   # a second assistant message after the tool
    assert out[12]["name"] == "agent.validation.warned" and out[13]["value"] == {"id": 7, "kind": "payment_draft"}
    assert all("timestamp" in e for e in out)
    await sink.emit_raw({"type": "agent.text.delta", "turn_id": "t1", "delta": "late"})     # after the run: dropped
    assert len(out) == 15


async def test_run_started_is_synthesised_and_an_error_ends_the_run():
    sink, out = _sink()
    await sink.emit_raw({"type": "agent.text.delta", "turn_id": "t", "delta": "a"})
    await sink.emit_raw({"type": "agent.run.error", "turn_id": "t", "message": "sidecar died"})
    await sink.emit_raw({"type": "agent.run.finished", "turn_id": "t"})
    await sink.finish()
    assert _kinds(out) == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_ERROR"]
    assert out[-1]["message"] == "sidecar died"
    # a run that never spoke still opens and closes
    sink, out = _sink()
    await sink.finish()
    assert _kinds(out) == ["RUN_STARTED", "RUN_FINISHED"]


async def test_sub_agent_events_become_steps_with_prefixed_tool_call_ids():
    sink, out = _sink()
    for e in [TurnEvent("run.started", "m"),
              TurnEvent("text.delta", "m", {"delta": "hỏi auditor"}),
              TurnEvent("sub.started", "m", {"agent": "auditor", "task": "ai nợ ai"}),
              TurnEvent("tool.start", "m", {"call_id": "c1", "name": "settle_period", "args": {}, "agent": "auditor"}),
              TurnEvent("tool.result", "m", {"call_id": "c1", "name": "settle_period", "status": "completed", "result": {"ok": True}, "agent": "auditor"}),
              TurnEvent("sub.finished", "m", {"agent": "auditor", "elapsed_ms": 3, "tools": ["settle_period"], "error": None}),
              TurnEvent("tool.start", "m", {"call_id": "c1", "name": "find_members", "args": {}}),
              TurnEvent("run.finished", "m")]:
        await sink.emit(e)
    await sink.finish()
    assert _kinds(out) == ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "STEP_STARTED",
                           "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT", "STEP_FINISHED",
                           "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "RUN_FINISHED"]
    assert out[4]["stepName"] == "auditor" and out[5]["toolCallId"] == "auditor/c1" and out[10]["toolCallId"] == "c1"


def test_from_legacy_is_the_inverse_of_to_legacy():
    for kind in sorted(EVENT_TYPES):
        data = {"id": 3, "kind": "x"} if kind == MESSAGE_REPUBLISHED else {"delta": "d", "call_id": "c"}
        event = TurnEvent(kind, None if kind == MESSAGE_REPUBLISHED else "t", data)
        back = from_legacy(to_legacy(event))
        assert back == event, kind
    assert from_legacy({"type": "pong"}) is None
