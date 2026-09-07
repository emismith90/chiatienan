"""Validation rules run (plan Task 6.2): paths, the three validators, the fold, gate 5."""
import pytest

from kernos.content import Models, PipelineEntry, ProfileSpec, ValidationRuleRef, blacklisted_changes
from kernos.kernel import Principal, Stage, TurnContext
from kernos.plugins import NonNegative, SumEquals, UniqueMembers, validators, values_at
from kernos.plugins.validate import PathError


def test_paths_are_the_documented_subset():
    obj = {"total": 5, "house": {"amount": 2}, "entries": [{"member": 1, "buy_in": 100}, {"member": 2}]}
    assert values_at(obj, "total") == [5] and values_at(obj, "house.amount") == [2]
    assert values_at(obj, "entries[*].buy_in") == [100] and values_at(obj, "nope") == [] and values_at(obj, "nope[*].x") == []
    with pytest.raises(PathError):
        values_at(obj, "entries[0].buy_in")
    with pytest.raises(PathError):
        values_at(obj, "Total")


def _ctx(args=None, result=None):
    ctx = TurnContext(space_id="s", principal=Principal(1, "An"), text="x")
    ctx.extras["tool_call"] = {"name": "propose_game", "args": args or {}, "result": result}
    return ctx


CHIPS = {"rule": "chips-conserved", "tool": "propose_game", "on_fail": "return_error",
         "left": "entries[*].buy_in", "right": ["entries[*].cash_out", "house"]}


async def test_sum_equals_reports_the_delta_and_fails_closed_on_non_integers():
    v = SumEquals(Stage.validate_args)
    assert v.id == "kernos.validate.sum_equals" and v.handles_money and v.stage is Stage.validate_args
    ok = {"entries": [{"member": 1, "buy_in": 500, "cash_out": 800}, {"member": 2, "buy_in": 500, "cash_out": 150}], "house": 50}
    assert await v.run(_ctx(ok), CHIPS) is None
    short = {"entries": [{"member": 1, "buy_in": 500, "cash_out": 800}, {"member": 2, "buy_in": 500, "cash_out": 100}]}
    verdict = await v.run(_ctx(short), CHIPS)
    assert verdict.severity == "block" and "delta +100" in verdict.reason and verdict.reason.startswith("chips-conserved:")
    assert (await v.run(_ctx(short), {**CHIPS, "tolerance": 100})) is None
    assert (await v.run(_ctx(short), {**CHIPS, "on_fail": "warn"})).severity == "warn"
    bad = await v.run(_ctx({"entries": [{"member": 1, "buy_in": "500", "cash_out": 500}]}), CHIPS)
    assert bad.severity == "block" and "non-integer" in bad.reason
    result_twin = SumEquals(Stage.validate_result)
    assert result_twin.id == "kernos.validate.sum_equals.result" and result_twin.stage is Stage.validate_result
    assert (await result_twin.run(_ctx(args={}, result=short), CHIPS)).severity == "block"


async def test_non_negative_and_unique_members():
    nn = NonNegative(Stage.validate_args)
    cfg = {"rule": "no-negatives", "tool": None, "on_fail": "return_error", "paths": ["entries[*].buy_in", "house"]}
    assert await nn.run(_ctx({"entries": [{"buy_in": 0}], "house": 5}), cfg) is None
    assert "must not be negative" in (await nn.run(_ctx({"entries": [{"buy_in": -1}]}), cfg)).reason
    um = UniqueMembers(Stage.validate_args)
    cfg = {"rule": "unique", "tool": None, "on_fail": "return_error", "path": "entries"}
    assert await um.run(_ctx({"entries": [{"member": 1}, {"member": 2}]}), cfg) is None
    assert "appears more than once" in (await um.run(_ctx({"entries": [{"member": 1}, {"member": 1}]}), cfg)).reason
    assert "appears more than once" in (await um.run(_ctx({"participants": [3, 3]}), {**cfg, "path": "participants"})).reason
    ids = sorted(p.id for p in validators())
    assert ids == ["kernos.validate.non_negative", "kernos.validate.non_negative.result", "kernos.validate.sum_equals",
                   "kernos.validate.sum_equals.result", "kernos.validate.unique_members", "kernos.validate.unique_members.result"]


def test_pipeline_dict_folds_tool_scope_rules_and_gate5_fences_them():
    spec = ProfileSpec(models=Models(text="m"), pipeline={"run": [PipelineEntry(id="r", version="1")]}, validation=[
        ValidationRuleRef(id="chips-conserved", scope="tool_args", plugin="kernos.validate.sum_equals", tool="propose_game",
                          config={"left": "entries[*].buy_in", "right": ["entries[*].cash_out", "house"]}, on_fail="return_error"),
        ValidationRuleRef(id="echo", scope="tool_result", plugin="kernos.validate.non_negative.result", tool="propose_game",
                          config={"paths": ["nets[*].amount"]}),
        ValidationRuleRef(id="polite", scope="reply", plugin="x.reply", config={}),
    ])
    d = spec.pipeline_dict()
    assert d["validate_args"] == [{"id": "kernos.validate.sum_equals", "version": "1",
                                   "config": {"left": "entries[*].buy_in", "right": ["entries[*].cash_out", "house"],
                                              "rule": "chips-conserved", "tool": "propose_game", "on_fail": "return_error"}}]
    assert d["validate_result"][0]["config"]["on_fail"] == "warn" and "validate" not in d       # reply rules are not folded
    loosened = spec.model_copy(update={"validation": [spec.validation[0].model_copy(update={"config": {**spec.validation[0].config, "tolerance": 5}}),
                                                     *spec.validation[1:]]})
    assert "validation[on_fail=block|scope=tool_*]" in blacklisted_changes(spec, loosened)
    reworded = spec.model_copy(update={"validation": [*spec.validation[:2], spec.validation[2].model_copy(update={"config": {"tone": "casual"}})]})
    assert blacklisted_changes(spec, reworded) == []                                            # a warn-level reply rule is an agent's to edit
