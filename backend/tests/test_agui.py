from types import SimpleNamespace

from app import agui


def _assistant(text):
    return SimpleNamespace(type="assistant",
                           message=SimpleNamespace(content=[SimpleNamespace(type="text", text=text)]))


def _tool(call_id, name, status, args=None, result=None):
    return SimpleNamespace(type="tool_call", call_id=call_id, name=name,
                           status=status, args=args, result=result)


def test_start_and_finish():
    assert agui.start("t1")[0]["type"] == "agent.run.started"
    assert agui.finish("t1")[0]["type"] == "agent.run.finished"
    assert agui.finish("t1", error="boom")[0]["type"] == "agent.run.error"


def test_assistant_text_delta():
    evs = agui.translate(_assistant("xin chào"), "t1")
    assert evs == [{"type": "agent.text.delta", "turn_id": "t1", "delta": "xin chào"}]


def test_tool_start_then_result():
    start = agui.translate(_tool("c1", "propose_meal", "running", args={"total": 100}), "t1")
    assert start[0]["type"] == "agent.tool.start"
    assert start[0]["name"] == "propose_meal"
    done = agui.translate(_tool("c1", "propose_meal", "completed", result={"ok": True}), "t1")
    assert done[0]["type"] == "agent.tool.result"
    assert done[0]["call_id"] == "c1"


def test_mcp_unwrap_names_the_real_tool():
    ev = agui.translate(_tool("c2", "mcp", "running",
                              args={"toolName": "find_members", "args": {"names": ["An"]}}), "t1")
    assert ev[0]["name"] == "find_members"
