import pytest

from kernos.kernel import (
    BasePlugin, Body, Draft, LegacyAgentEventSink, Pipeline, PipelineError, Principal,
    Stage, TurnContext, TurnEvent, Verdict, flush, to_legacy,
)


class Rec(BasePlugin):
    """A plugin that appends its name to ctx.extras['seen'] and returns what it is told."""

    def __init__(self, id_, stage, ret=None, raise_=None):
        self.id, self.stage, self._ret, self._raise = id_, Stage(stage), ret, raise_

    async def run(self, ctx, config):
        ctx.extras.setdefault("seen", []).append((self.id, dict(config)))
        if self._raise:
            raise self._raise
        return self._ret


def _ctx(**kw):
    return TurnContext(space_id="s1", principal=Principal(1, "An"), text="hi", **kw)


def _stages(**extra):
    base = {
        Stage.model: [(Rec("m", "model"), {})],
        Stage.run: [(Rec("r", "run"), {})],
        Stage.render: [(Rec("d", "render"), {})],
    }
    base.update(extra)
    return base


async def test_stages_run_in_order_and_plugins_in_listed_order():
    p = Pipeline(_stages(**{
        Stage.context: [(Rec("c1", "context"), {"a": 1}), (Rec("c2", "context"), {})],
        Stage.after: [(Rec("z", "after"), {})],
    }))
    ctx = await p.run(_ctx())
    assert [s for s, _ in ctx.extras["seen"]] == ["c1", "c2", "m", "r", "d", "z"]
    assert ctx.extras["seen"][0][1] == {"a": 1}
    assert [t["stage"] for t in ctx.trace] == ["context", "context", "model", "run", "render", "after"]
    assert all(t["outcome"] == "ok" and t["ms"] >= 0 for t in ctx.trace)


async def test_block_replaces_outcome_and_skips_remaining_validators():
    replacement = Body("⚠️ not recorded")
    p = Pipeline(_stages(**{
        Stage.validate: [
            (Rec("warn", "validate", ret=Verdict(False, "warn", "hmm")), {}),
            (Rec("block", "validate", ret=Verdict(False, "block", "forged", replacement)), {}),
            (Rec("never", "validate"), {}),
        ],
        Stage.persist: [(Rec("p", "persist"), {})],
    }))
    ctx = _ctx()
    ctx.outcome = Body("Đã ghi #14")
    await p.run(ctx)
    assert ctx.outcome is replacement and ctx.stopped
    seen = [s for s, _ in ctx.extras["seen"]]
    assert "never" not in seen and "p" in seen          # persist still runs
    by_plugin = {t["plugin"]: t for t in ctx.trace}
    assert by_plugin["warn"]["outcome"] == "warn" and by_plugin["warn"]["reason"] == "hmm"
    assert by_plugin["block"]["outcome"] == "block"
    assert by_plugin["never"]["outcome"] == "skipped"


async def test_exception_is_recorded_then_reraised():
    p = Pipeline(_stages(**{Stage.context: [(Rec("boom", "context", raise_=RuntimeError("x")), {})]}))
    ctx = _ctx()
    with pytest.raises(RuntimeError):
        await p.run(ctx)
    assert ctx.trace[-1]["outcome"] == "error" and "RuntimeError: x" in ctx.trace[-1]["error"]


@pytest.mark.parametrize("missing", [Stage.model, Stage.run, Stage.render])
def test_single_owner_stages_need_exactly_one_plugin(missing):
    stages = _stages()
    stages[missing] = []
    with pytest.raises(PipelineError, match=str(missing)):
        Pipeline(stages)
    stages[missing] = [(Rec("a", missing), {}), (Rec("b", missing), {})]
    with pytest.raises(PipelineError):
        Pipeline(stages)


def test_plugin_listed_under_the_wrong_stage_is_rejected():
    with pytest.raises(PipelineError, match="is a 'after' plugin"):
        Pipeline(_stages(**{Stage.context: [(Rec("z", "after"), {})]}))


async def test_pending_events_are_not_flushed_by_the_pipeline():
    class Pending(Rec):
        async def run(self, ctx, config):
            ctx.pending_events.append(TurnEvent("message.republished", data={"id": 3, "kind": "expense_draft"}))
    p = Pipeline(_stages(**{Stage.persist: [(Pending("p", "persist"), {})]}))
    ctx = await p.run(_ctx())
    assert len(ctx.pending_events) == 1
    got = []
    async def emit(d): got.append(d)
    await flush(ctx.pending_events, LegacyAgentEventSink(emit))
    assert got == [{"type": "message", "id": 3, "kind": "expense_draft"}]
    assert ctx.pending_events == []


def test_legacy_mapping_keeps_the_frozen_agent_names():
    assert to_legacy(TurnEvent("run.started", "t1")) == {"type": "agent.run.started", "turn_id": "t1"}
    assert to_legacy(TurnEvent("tool.result", "t1", {"name": "x", "status": "completed"})) == {
        "type": "agent.tool.result", "turn_id": "t1", "name": "x", "status": "completed"}
    with pytest.raises(ValueError):
        TurnEvent("nope")


async def test_per_call_validators_are_consulted_not_run():
    p = Pipeline(_stages(**{
        Stage.validate_args: [(Rec("va", "validate_args", ret=Verdict(False, "block", "bad")), {})],
    }))
    ctx = await p.run(_ctx())
    assert "va" not in [s for s, _ in ctx.extras["seen"]]            # not a stage
    verdict = await p.validate(Stage.validate_args, ctx)
    assert verdict is not None and verdict.reason == "bad"
    with pytest.raises(PipelineError):
        await p.validate(Stage.validate, ctx)


def test_describe_lists_pipeline_in_execution_order():
    p = Pipeline(_stages(**{Stage.context: [(Rec("c", "context"), {"k": 1})]}))
    assert [d["plugin"] for d in p.describe()] == ["c", "m", "r", "d"]
    assert p.describe()[0]["config"] == {"k": 1}


def test_outcome_types():
    assert Draft("expense_draft", {"a": 1}).kind == "expense_draft"
    assert Body("x").claimed_by_pack is False
    assert Verdict.passed().ok
