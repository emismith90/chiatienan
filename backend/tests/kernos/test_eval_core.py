"""`kernos.eval`: cases, the run identity, the grader registry, the runner (plan Task 4.2)."""
from types import SimpleNamespace

import pytest


from kernos.engine.base import ToolInvocation, TurnResult
from kernos.eval import EvalCase, GraderRegistry, Runner, ToolSelection, Verdict, spec_sha
from tests.kernos.test_spec import _spec


def test_eval_case_round_trips_and_ignores_unknown_keys():
    c = EvalCase(id="G1", source="meals", day="2026-07-20", actor="m1", message="@bot x",
                 expect={"tools": ["propose_meal"]}, images=[{"data": "AA==", "mimeType": "image/png"}])
    d = c.to_dict()
    assert d["review"] is False and d["tags"] == [] and d["images"][0]["mimeType"] == "image/png"
    assert EvalCase.from_dict({**d, "extra": 1}) == c


def test_spec_sha_ignores_eval_and_tracks_the_rest():
    spec = _spec()
    base = spec_sha(spec)
    assert base == spec_sha(spec.stored()) and len(base) == 64
    assert spec_sha(spec.model_copy(update={"eval": spec.eval.model_copy(update={"gate": {"tool_selection": 0.5}})})) == base
    assert spec_sha(spec.model_copy(update={"prompt": spec.prompt.model_copy(update={"body": "changed"})})) != base


class Always:
    blocking = False

    def __init__(self, passed):
        self._p = passed

    def grade(self, case, record, world):
        return Verdict(self._p, "stub")


class Raises:
    blocking = True

    def grade(self, case, record, world):
        raise RuntimeError("no db")


def test_registry_builds_named_graders_and_rejects_unknown_ids():
    reg = GraderRegistry()
    reg.register("acme.eval.tool_selection", lambda cfg, *, judge=None: ToolSelection(cfg))
    reg.register("acme.eval.always", lambda cfg, *, judge=None: Always(cfg.get("passed")))
    name, g = reg.build({"plugin": "acme.eval.tool_selection"})
    assert name == "tool_selection" and isinstance(g, ToolSelection) and g.blocking
    name, g = reg.build({"plugin": "acme.eval.always", "name": "prose_quality", "config": {"passed": True}})
    assert name == "prose_quality" and g.grade(None, {}, None).passed is True
    with pytest.raises(KeyError, match="no grader"):
        reg.build({"plugin": "nope"})
    assert reg.ids() == ["acme.eval.always", "acme.eval.tool_selection"]


def _case(id_, tools=None, review=False):
    return EvalCase(id=id_, source="stub", day="2026-07-20", actor="a1", message=f"@bot {id_}",
                    expect={"tools": tools} if tools else {}, review=review)


async def test_runner_grades_isolates_skips_review_and_summarises(db):
    closed = []

    def world_factory(case):
        return SimpleNamespace(space_id=7, ids={"a1": 1, "a2": 2}, db=db, close=lambda: closed.append(case.id))

    async def run_turn(case, world, spec):
        if case.id == "boom":
            raise RuntimeError("engine down")
        called = "settle_period" if case.id == "wrong" else "propose_meal"
        return TurnResult(final_text="ok", turn_id=f"t-{case.id}", stats={"tokens": 5, "cost": 0.001},
                          tools=[ToolInvocation(called, {"total": 1}, {"ok": True})])

    graders = [("tool_selection", ToolSelection({"compared_args": []})), ("raises", Raises()), ("always", Always(True))]
    runner = Runner(graders, world_factory=world_factory, run_turn=run_turn, repeat=2, judge_model=None)
    cases = [_case("pass", ["propose_meal"]), _case("wrong", ["propose_meal"]), _case("none"),
             _case("skip", ["propose_meal"], review=True), _case("boom", ["propose_meal"])]
    run = await runner.run(cases, spec=None)
    records = run["records"]
    assert len(records) == 10 and sorted(closed) == sorted(["pass", "wrong", "none", "boom"] * 2)
    by_id = {(r["case_id"], r["rep"]): r for r in records}
    assert by_id[("pass", 0)]["grades"]["tool_selection"]["passed"] is True and by_id[("pass", 0)]["room_id"] == 7
    assert by_id[("pass", 0)]["member_ids"] == {"a1": 1, "a2": 2} and by_id[("pass", 0)]["sender_member_id"] == 1
    assert by_id[("wrong", 1)]["grades"]["tool_selection"]["passed"] is False
    assert by_id[("none", 0)]["grades"]["tool_selection"]["passed"] is None
    assert by_id[("skip", 0)]["skipped"] == "review" and by_id[("skip", 0)]["grades"] == {}
    boom = by_id[("boom", 0)]
    assert boom["error"] == "RuntimeError: engine down" and boom["grades"]["tool_selection"]["passed"] is False
    assert boom["grades"]["raises"] == {"passed": None, "reason": "grader raised: RuntimeError('no db')", "raised": True}
    s = run["summary"]
    assert s["records"] == 10 and s["skipped_review"] == 2 and s["repeat"] == 2 and s["judge_model"] is None
    ts, rs, al = s["graders"]
    assert ts == {"name": "tool_selection", "blocking": True, "passed": 2, "failed": 4,
                  "ungraded_no_expectation": 2, "ungraded_grader_raised": 0, "rate": 2 / 6}
    assert rs["ungraded_grader_raised"] == 8 and rs["rate"] is None and rs["blocking"] is True
    assert al["passed"] == 8 and al["rate"] == 1.0 and al["blocking"] is False
    assert s["cost_latency"]["n"] == 8 and s["cost_latency"]["error_n"] == 2 and s["cost_latency"]["stats_n"] == 6
