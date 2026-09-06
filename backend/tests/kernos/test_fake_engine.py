"""``ScriptedEngine`` is the real ``PiEngine`` over a scripted bridge (Phase 9 review F11)."""
import json

from kernos.engine import EngineSpec, ScriptedBridge, ScriptedEngine, ToolSpec

SPEC = EngineSpec(model="m", vision_model=None, thinking="off", builtin_tools=[], max_tools=3, max_seconds=9,
                  cwd="/c", agent_dir="/a", system="S")


async def test_the_scripted_engine_reproduces_a_sidecar_turn_field_for_field():
    engine = ScriptedEngine([
        {"type": "agent.run.started", "turn_id": "t"},
        {"type": "tool_call", "call_id": "c1", "name": "say_hello", "args": {"name": "An"}},
        {"type": "tool_call", "call_id": "c2", "name": "cms_log", "args": {"message": "x"}},
        {"type": "agent.run.finished", "turn_id": "t"},
        {"type": "turn_done", "final_text": "Hello, An!", "error": None, "capped": True, "stats": {"tokens": 5}},
    ])
    seen, calls = [], []

    async def emit(e):
        seen.append(e["type"])

    async def call_tool(name, args):
        calls.append((name, args))
        if name == "cms_log":
            return {"ok": True, "note": "prose 999k", "_record": {"ok": True}}
        return {"ok": True, "greeting": f"Hello, {args['name']}!"}

    result = await engine.run(SPEC, turn_id="t", message="hi", images=None,
                              tools=[ToolSpec("say_hello", "d", {"type": "object"})], call_tool=call_tool, emit=emit)
    assert result.final_text == "Hello, An!" and result.capped is True and result.stats == {"tokens": 5} and result.error is None
    assert [(i.name, i.result) for i in result.tools] == [("say_hello", {"ok": True, "greeting": "Hello, An!"}), ("cms_log", {"ok": True})]
    assert seen == ["agent.run.started", "agent.run.finished"] and calls[0] == ("say_hello", {"name": "An"})
    run = engine.bridge.runs[0]
    assert run["type"] == "run" and run["req_id"] == "run-t" and run["system"] == "S" and run["max_tools"] == 3
    assert run["tools"] == [{"name": "say_hello", "description": "d", "schema": {"type": "object"}}]
    assert engine.bridge.tool_result("c2") == {"ok": True, "note": "prose 999k"}          # sent without `_record`
    assert json.loads(engine.bridge.sent[0]["content"])["greeting"] == "Hello, An!"


async def test_a_bridge_with_no_script_left_is_a_fatal_not_a_hang():
    bridge = ScriptedBridge([{"type": "turn_done", "final_text": "one", "error": None}])
    engine = ScriptedEngine.__new__(ScriptedEngine)
    engine.bridge = bridge
    from kernos.engine.pi.engine import PiEngine
    PiEngine.__init__(engine, bridge)

    async def call_tool(name, args):
        return {}

    first = await engine.run(SPEC, turn_id="a", message="m", images=None, tools=[], call_tool=call_tool, emit=None)
    second = await engine.run(SPEC, turn_id="b", message="m", images=None, tools=[], call_tool=call_tool, emit=None)
    assert first.final_text == "one" and second.error == "scripted bridge has no script for this run"
