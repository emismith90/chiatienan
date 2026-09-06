"""The example host runs a turn with no chiatienan module on its path (design §12.3; plan
Task 9.3, review F10)."""
import importlib
import sys

import pytest
from fastapi.testclient import TestClient

BLOCKED = ("app", "packs", "ledger_core", "bench")


class _Guard:
    """A meta-path finder that refuses the host's modules while the example imports."""

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"the example host imported {name}; it must run on kernos alone")
        return None


@pytest.fixture
def host_module():
    for name in [m for m in sys.modules if m == "examples" or m.startswith("examples.")]:
        sys.modules.pop(name)
    guard = _Guard()
    sys.meta_path.insert(0, guard)
    try:
        module = importlib.import_module("examples.minimal_host.host")
    finally:
        sys.meta_path.remove(guard)
    return module


def test_one_turn_end_to_end_with_agui_events_and_the_admin_api(host_module):
    client = TestClient(host_module.create_app())
    script = [
        {"type": "agent.run.started", "turn_id": "t"},
        {"type": "agent.text.delta", "turn_id": "t", "delta": "Greeting…"},
        {"type": "agent.tool.start", "turn_id": "t", "call_id": "c1", "name": "say_hello", "args": {"name": "An"}},
        {"type": "tool_call", "call_id": "c1", "name": "say_hello", "args": {"name": "An"}},
        {"type": "agent.tool.result", "turn_id": "t", "call_id": "c1", "name": "say_hello", "status": "completed", "result": {"ok": True}},
        {"type": "agent.run.finished", "turn_id": "t"},
        {"type": "turn_done", "final_text": "Said hello.", "error": None, "capped": False, "stats": None},
    ]
    r = client.post("/spaces/s1/turns", json={"text": "@hello greet An", "script": script})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"]["body"] == "Hello, An!" and body["reply"]["attachments"] == {"type": "greeting"}   # the pack's body, not the prose
    assert [e["type"] for e in body["events"]] == [
        "RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT", "RUN_FINISHED"]
    assert body["events"][0]["threadId"] == "s1" and body["events"][4]["toolCallName"] == "say_hello"
    # the admin API is mounted and the turn was traced
    registry = client.get("/admin/registry").json()
    assert any(p["id"] == "kernos.run.engine" for p in registry)
    turns = client.get("/admin/spaces/s1/turns").json()
    assert len(turns) == 1 and turns[0]["summary"]["tools"] == ["say_hello"]
    trace = client.get(f"/admin/spaces/s1/turns/{turns[0]['id']}").json()
    assert trace["tools"][0]["result"] == {"ok": True, "greeting": "Hello, An!"}
    resolved = client.get("/admin/spaces/s1/resolved").json()["resolution"]
    assert resolved["agent"]["slug"] == "hello" and resolved["source"] == "default"
    # no chiatienan module reached the process through the host
    kernel = host_module.app.state.kernel
    assert {p.id for p in kernel.packs.list()} == {"collections", "delegation", "hello", "os_admin"}
    assert kernel.reserved_tool_names() >= {"say_hello", "cms_publish"}
