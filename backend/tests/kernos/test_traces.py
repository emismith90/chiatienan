"""The turn trace (plan Task 4.1): the in-memory store, the summary, the plugin."""
from datetime import datetime, timezone

from kernos.adapters import TraceStore
from kernos.adapters.memory import InMemoryTraces
from kernos.engine.base import ToolInvocation, TurnResult
from kernos.kernel import Body, Draft, Principal, Stage, TurnContext
from kernos.plugins import Trace, summarize, tool_calls

NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)


def _ctx(**kw):
    return TurnContext(space_id="7", principal=Principal(3, "An"), text="@bot hi", **kw)


def test_in_memory_traces_write_list_get_and_prune():
    t = InMemoryTraces()
    assert isinstance(t, TraceStore)
    old = t.write("7", "t-old", started="2026-08-01T00:00:00+00:00", finished="2026-08-01T00:00:01+00:00",
                  summary={"a": 1}, tools=[], trace=[])
    new = t.write("7", None, started="2026-09-06T08:00:00+00:00", finished="2026-09-06T08:00:01+00:00",
                  summary={"a": 2}, tools=[{"name": "x"}], trace=[{"plugin": "p"}], keep_days=30)
    assert [r["id"] for r in t.list("7")] == [new["id"]]              # the old row was pruned
    assert "tools" not in t.list("7")[0] and t.list("other") == []
    assert t.get("7", new["id"])["tools"] == [{"name": "x"}] and t.get("7", "t-old") is None
    t.write("7", "t-2", started="2026-09-06T08:00:00+00:00", finished="2026-09-06T08:00:02+00:00",
            summary={}, tools=[], trace=[])
    assert t.get("7", "t-2")["turn_id"] == "t-2" and t.get("8", "t-2") is None
    assert old["id"] != new["id"]


def test_summary_tolerates_a_turn_without_result_or_outcome():
    ctx = _ctx()
    ctx.record(Stage.context, "kernos.context.memory", "1", 12.5, "error", error="RuntimeError: boom")
    s = summarize(ctx)
    assert s["tools"] == [] and s["outcome"] is None and s["error"] == "RuntimeError: boom"
    assert s["elapsed_ms"] == 12.5 and s["capped"] is False and s["principal"] == "3"
    assert tool_calls(ctx) == []


def test_summary_of_a_draft_turn_names_tools_verdicts_and_the_card():
    ctx = _ctx()
    ctx.result = TurnResult(final_text="", turn_id="t1", stats={"tokens": 1200, "cost": 0.002},
                            tools=[ToolInvocation("find_members", {"names": ["An"]}, {"ok": True}),
                                   ToolInvocation("propose_meal", {"total": 90000}, {"ok": True, "type": "expense_draft"})])
    ctx.outcome = Draft("expense_draft", {"bill_total": 90000})
    ctx.record(Stage.validate, "app.validate.unbacked_amounts", "1", 1.0, "warn", reason="stray 5k")
    s = summarize(ctx)
    assert s["tools"] == ["find_members", "propose_meal"] and s["tokens"] == 1200 and s["cost"] == 0.002
    assert s["outcome"] == {"kind": "draft", "draft_kind": "expense_draft"}
    assert s["verdicts"] == [{"plugin": "app.validate.unbacked_amounts", "outcome": "warn", "reason": "stray 5k"}]
    calls = tool_calls(ctx)
    assert calls[1] == {"name": "propose_meal", "args": {"total": 90000},
                        "result": {"ok": True, "type": "expense_draft"}, "from_agent": None}
    ctx.outcome = Body("ok", {"type": "settlement"}, claimed_by_pack=True)
    assert summarize(ctx)["outcome"] == {"kind": "body", "claimed_by_pack": True, "attachment_type": "settlement"}


async def test_trace_plugin_writes_one_row_with_started_before_finished():
    store = InMemoryTraces()
    plugin = Trace(store, now=lambda: NOW)
    ctx = _ctx()
    ctx.result = TurnResult(final_text="hi", turn_id="t9")
    ctx.record(Stage.run, "app.run.legacy", "1", 1500.0, "ok")
    await plugin.run(ctx, {"keep_days": 7})
    row = store.get("7", "t9")
    assert row["finished"] == "2026-09-06T08:00:00+00:00" and row["started"] == "2026-09-06T07:59:58+00:00"
    assert row["summary"]["tools"] == [] and row["trace"][0]["plugin"] == "app.run.legacy"
    assert ctx.extras["trace_row_id"] == row["id"]
