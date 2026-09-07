from kernos.engine import EngineSpec, ToolInvocation, ToolSpec, TurnResult


def _spec(**kw):
    base = dict(model="m", vision_model="v", thinking="medium", builtin_tools=["read"],
                max_tools=40, max_seconds=120, cwd="/c", agent_dir="/a", system="SYS",
                skills=[{"name": "s", "description": "", "body": "b"}],
                context_files=[{"path": "r", "content": "c"}])
    base.update(kw)
    return EngineSpec(**base)


def test_run_command_has_exactly_todays_fields_when_settings_and_extensions_are_empty():
    cmd = _spec().to_run_command(req_id="run-1", turn_id="1", message="hi", images=None,
                                 tools=[ToolSpec("t", "d", {"type": "object"})])
    assert cmd == {
        "type": "run", "req_id": "run-1", "turn_id": "1", "system": "SYS", "message": "hi",
        "images": [], "tools": [{"name": "t", "description": "d", "schema": {"type": "object"}}],
        "skills": [{"name": "s", "description": "", "body": "b"}],
        "context_files": [{"path": "r", "content": "c"}],
        "cwd": "/c", "agent_dir": "/a", "model": "m", "vision_model": "v", "thinking": "medium",
        "builtin_tools": ["read"], "max_tools": 40, "max_seconds": 120,
    }
    assert "settings" not in cmd and "extensions" not in cmd


def test_settings_and_extensions_ride_the_command_only_when_set():
    cmd = _spec(settings={"compaction": {"enabled": False}},
                extensions=[{"id": "x", "config": {}}]).to_run_command(
        req_id="r", turn_id="t", message="m", images=[{"data": "…"}], tools=[{"name": "n"}])
    assert cmd["settings"] == {"compaction": {"enabled": False}}
    assert cmd["extensions"] == [{"id": "x", "config": {}}]
    assert cmd["images"] == [{"data": "…"}] and cmd["tools"] == [{"name": "n"}]


def test_system_none_goes_out_as_empty_string():
    assert _spec(system=None).to_run_command(req_id="r", turn_id="t", message="m",
                                             images=None, tools=[])["system"] == ""


def test_turn_result_shape_is_the_frozen_one():
    tr = TurnResult(final_text="x", tools=[ToolInvocation("a", {}, {"ok": True, "v": 1}),
                                            ToolInvocation("a", {}, {"ok": False})])
    assert tr.error is None and tr.capped is False and tr.stats is None
    assert tr.last_result("a") == {"ok": True, "v": 1}
    assert tr.all_results("a") == [{"ok": True, "v": 1}]
    assert ToolInvocation("a", {}, {}).from_agent is None
