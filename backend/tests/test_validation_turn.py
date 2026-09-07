"""A profile's tool-scope rule refuses a tool call before it runs (plan Task 6.2)."""
import pytest

from app import agent
from app.kernel import kernel_for
from app.tools import ToolContext
from kernos.content import ValidationRuleRef
from kernos.content.errors import GateError
from kernos.kernel import Stage
from tests.test_agent import FakeBridge
from tests.test_ledger import _seed_room

RULE = {"id": "total-is-items", "scope": "tool_args", "plugin": "kernos.validate.sum_equals", "tool": "propose_meal",
        "config": {"left": "total", "right": ["items[*].amount"]}, "on_fail": "return_error"}


def _publish(k, validation):
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    k.store.update_draft(d["id"], {"validation": validation}, actor="admin")
    return k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="test")


async def test_the_rule_refuses_a_mismatched_call_and_the_trace_shows_it(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    k = kernel_for(db)
    _publish(k, [RULE])
    spec = k.resolve(room_id)
    pipeline = k.pipeline_for(spec)
    assert [e["plugin"] for e in pipeline.describe() if e["stage"] == "validate_args"] == ["kernos.validate.sum_equals"]

    script = [
        {"type": "tool_call", "call_id": "c1", "name": "propose_meal",
         "args": {"participants": m, "total": 300_000, "items": [{"member": m[0], "amount": 100_000}, {"member": m[1], "amount": 100_000}]}},
        {"type": "tool_call", "call_id": "c2", "name": "propose_meal",
         "args": {"participants": m, "total": 200_000, "items": [{"member": m[0], "amount": 100_000}, {"member": m[1], "amount": 100_000}]}},
        {"type": "turn_done", "final_text": "ok", "error": None},
    ]
    fake = FakeBridge(script)
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
    from app import chat
    reply = await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix 300k")
    trace = k.store.get_trace(str(room_id), None) or k.store.list_traces(str(room_id))[0]
    full = k.store.get_trace(str(room_id), trace["id"])
    calls = full["tools"]
    assert calls[0]["result"]["ok"] is False and "total-is-items" in calls[0]["result"]["error"] and "delta +100,000" in calls[0]["result"]["error"]
    assert calls[1]["result"]["ok"] is True and calls[1]["result"]["bill_total"] == 200_000
    rows = [(t["outcome"], t.get("tool"), t.get("rule")) for t in full["trace"] if t["stage"] == "validate_args"]
    assert rows == [("block", "propose_meal", "total-is-items"), ("ok", "propose_meal", "total-is-items")]
    assert full["summary"]["stopped"] is False and reply.kind == "expense_draft"        # the second call made the card


async def test_without_a_rule_nothing_is_consulted(db, monkeypatch):
    room_id, m = _seed_room(db, 2)
    fake = FakeBridge([{"type": "tool_call", "call_id": "c", "name": "propose_meal", "args": {"participants": m, "total": 300_000}},
                       {"type": "turn_done", "final_text": "ok", "error": None}])
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=m[0])
    result = await agent.run_turn("x", ctx)
    assert result.tools[0].result["type"] == "expense_draft" and ctx.validate_call is None


def test_gate1_refuses_a_rule_on_an_unknown_validator_tool_or_path(db):
    k = kernel_for(db)
    for patch, needle in [
        ({**RULE, "plugin": "kernos.validate.nope"}, "kernos.validate.nope"),
        ({**RULE, "tool": "not_a_tool"}, "not_a_tool"),
        ({**RULE, "config": {"left": "entries[0].x", "right": ["y"]}}, "does not match"),
        ({**RULE, "plugin": "kernos.validate.sum_equals.result"}, "is a 'validate_result' plugin"),
    ]:
        with pytest.raises(GateError) as exc:
            _publish(k, [patch])
        assert any(needle in msg for _, msg in exc.value.failures), (needle, exc.value.failures)
    # an agent may not loosen or remove a tool-scope rule
    v = _publish(k, [RULE])
    d = k.store.create_draft(k.seed_report["profile_id"], actor="agent:phoenix")
    k.store.update_draft(d["id"], {"validation": [{**RULE, "config": {**RULE["config"], "tolerance": 10}}]}, actor="agent:phoenix")
    with pytest.raises(GateError) as exc:
        k.store.publish(d["id"], actor="agent:phoenix", gates=k.gates, override_reason="t")
    assert any(f[0] == "reflexivity" for f in exc.value.failures) and v["status"] == "published"
