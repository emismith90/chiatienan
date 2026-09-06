"""The lunch suite as content, runs as jobs, gate 4 over stored runs, eval capture
(plan Task 4.3)."""
import pytest

import app.agent as agent_mod
from app import chat, evalhost
from app.agent import ToolInvocation, TurnResult
from app.kernel import Kernel, kernel_for
from app.tools import ToolContext, build_tools
from bench import corpus
from kernos.content import ProfileSpec
from kernos.content.errors import GateError
from kernos.eval import EvalCase, Runner, spec_sha
from tests.test_ledger import _seed_room

ADMIN = {"X-Admin-Password": "test-admin-pw"}


def _import(db):
    k = kernel_for(db)
    bid = k.seed_report["business_id"]
    report = evalhost.import_lunch_suite(k.store, bid)
    return k, bid, report


def test_import_is_the_typical_corpus_with_ids_and_images_preserved_and_idempotent(db):
    k, bid, report = _import(db)
    expected = corpus.load("typical")
    assert report["cases"] == len(expected) and report["cases"] in (23, 37)
    rows = k.store.list_cases(bid, source="imported")
    assert [r["slug"] for r in rows] == [c.id for c in expected] and all(r["review"] is False for r in rows)
    bills = [c for c in expected if c.source == "bills"]
    if bills:
        stored = k.store.get_case(bid, bills[0].id)["case"]
        assert stored["images"] and stored["images"][0]["data"] and stored["had_images"] is True
    suite = k.store.get_suite(bid, "lunch-typical")
    assert [g["name"] for g in suite["graders"]] == ["tool_selection", "ledger_state", "prose_quality"]
    assert suite["judge"] == {"model": None, "rubric": "prose"} and "Vietnamese" in k.store.get_rubric(bid, "prose")["body"]
    again = evalhost.import_lunch_suite(k.store, bid)
    assert again["cases"] == report["cases"] and len(k.store.list_cases(bid)) == report["cases"]


def _mini_suite(k, bid, slugs, graders=None, name="mini"):
    return k.store.put_suite(bid, name, actor="admin", case_slugs=slugs,
                             graders=graders or evalhost.LUNCH_GRADERS[:2])


def _draft(k, spec_patch):
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    return k.store.update_draft(d["id"], spec_patch, actor="admin")


async def _oracle(case, world, spec):
    """Calls the expected money tool with the case's own (resolved) arguments."""
    from packs.lunch_ledger.eval import tool_selection
    resolved = tool_selection({}).resolve_args(case, world.ids)
    name = case.expect["tools"][0]
    args = dict(resolved.get(name) or {})
    ctx = ToolContext(db=world.db, room_id=world.space_id, sender_member_id=world.ids[case.actor], sender_name="x")
    res = build_tools(ctx)[name].execute(args)
    return TurnResult(final_text="", turn_id=f"t-{case.id}", tools=[ToolInvocation(name, args, res)])


async def _wrong(case, world, spec):
    return TurnResult(final_text="uh", turn_id="t", tools=[ToolInvocation("settle_period", {}, {"ok": True, "transfers": []})])


async def test_gate4_needs_a_matching_completed_run_and_reads_blocking_graders(db):
    k, bid, _ = _import(db)
    _mini_suite(k, bid, ["G1", "G2"])
    v = _draft(k, {"eval": {"suites": ["mini"]}})
    with pytest.raises(GateError) as exc:
        k.store.publish(v["id"], actor="admin", gates=k.gates, override_reason="t")
    assert any("no completed run" in m for _, m in exc.value.failures)

    bad = await evalhost.run_suite(k, "mini", v["id"], run_turn=_wrong, world_factory=evalhost.world_factory)
    assert bad["status"] == "done" and bad["summary"]["graders"][0]["failed"] == 2
    with pytest.raises(GateError) as exc:
        k.store.publish(v["id"], actor="admin", gates=k.gates, override_reason="t")
    msgs = [m for _, m in exc.value.failures]
    assert any("tool_selection pass rate 0%" in m and "2 of 2 failed" in m for m in msgs), msgs

    good = await evalhost.run_suite(k, "mini", v["id"], run_turn=_oracle, world_factory=evalhost.world_factory)
    assert good["status"] == "done", good.get("error")
    ts, ls = good["summary"]["graders"]
    assert ts == {"name": "tool_selection", "blocking": True, "passed": 2, "failed": 0,
                  "ungraded_no_expectation": 0, "ungraded_grader_raised": 0, "rate": 1.0}
    assert ls["passed"] == 2 and ls["blocking"] is True
    assert good["spec_sha"] == spec_sha(ProfileSpec.model_validate(k.store.get_version(v["id"])["spec"]))
    published = k.store.publish(v["id"], actor="admin", gates=k.gates, override_reason="t")
    assert published["status"] == "published"

    # a threshold edit keeps the run valid; a prompt edit invalidates it
    v2 = _draft(k, {"eval": {"suites": ["mini"], "gate": {"tool_selection": 0.5}}})
    assert k.store.publish(v2["id"], actor="admin", gates=k.gates, override_reason="t")["status"] == "published"
    v3 = _draft(k, {"prompt": {"body": "new prompt"}})
    with pytest.raises(GateError):
        k.store.publish(v3["id"], actor="admin", gates=k.gates, override_reason="t")
    # rollback skips the gate even when no run matches any more
    k.store.list_runs = lambda **kw: []
    assert k.store.rollback(k.seed_report["profile_id"], published["version"], actor="admin", gates=k.gates,
                            override_reason="t")["status"] == "published"


async def test_a_grader_that_raises_or_grades_nothing_blocks_and_prose_alone_does_not(db):
    k, bid, _ = _import(db)

    class Boom:
        blocking = True

        def grade(self, case, record, world):
            raise RuntimeError("no db")

    k.graders.register("test.boom", lambda cfg, *, judge=None: Boom())
    _mini_suite(k, bid, ["G1"], graders=[{"plugin": "test.boom"}], name="boom")
    v = _draft(k, {"eval": {"suites": ["boom"]}})
    run = await evalhost.run_suite(k, "boom", v["id"], run_turn=_oracle, world_factory=evalhost.world_factory)
    assert run["summary"]["graders"][0]["ungraded_grader_raised"] == 1
    with pytest.raises(GateError) as exc:
        k.store.publish(v["id"], actor="admin", gates=k.gates, override_reason="t")
    assert any("raised on 1 case" in m for _, m in exc.value.failures)

    # prose fails (an invented amount), tool_selection passes: not blocked
    k.store.put_case(bid, "chat", EvalCase(id="chat", source="t", day="2026-07-20", actor="m1",
                                            members=corpus.MEALS_MEMBERS, message="@bot ai đây",
                                            expect={"tools": ["find_members"]}).to_dict(), actor="admin")
    _mini_suite(k, bid, ["chat"], graders=[evalhost.LUNCH_GRADERS[0], evalhost.LUNCH_GRADERS[2]], name="prose")

    async def chatty(case, world, spec):
        return TurnResult(final_text="bạn nợ 999,999đ", turn_id="t",
                          tools=[ToolInvocation("find_members", {"names": ["An"]}, {"ok": True})])

    v = _draft(k, {"eval": {"suites": ["prose"]}})
    run = await evalhost.run_suite(k, "prose", v["id"], run_turn=chatty, world_factory=evalhost.world_factory)
    ts, pq = run["summary"]["graders"]
    assert ts["passed"] == 1 and pq["failed"] == 1 and pq["blocking"] is False
    assert k.store.publish(v["id"], actor="admin", gates=k.gates, override_reason="t")["status"] == "published"

    # a suite naming an unknown grader: the run fails, the gate refuses
    _mini_suite(k, bid, ["G1"], graders=[{"plugin": "nope"}], name="unknown")
    v = _draft(k, {"eval": {"suites": ["unknown"]}})
    run = await evalhost.run_suite(k, "unknown", v["id"], run_turn=_oracle, world_factory=evalhost.world_factory)
    assert run["status"] == "failed" and "no grader 'nope'" in run["error"]
    with pytest.raises(GateError) as exc:
        k.store.publish(v["id"], actor="admin", gates=k.gates, override_reason="t")
    assert any("the latest run failed" in m for _, m in exc.value.failures)


async def test_the_real_run_turn_drives_the_candidate_pipeline_in_the_case_world(db, monkeypatch):
    k, bid, _ = _import(db)
    case = EvalCase.from_dict(k.store.get_case(bid, "G1")["case"])
    seen = {}

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        seen["system"] = ctx.system_override
        seen["today"] = __import__("app.clock", fromlist=["today_ict"]).today_ict().isoformat()
        args = {"participants": list(ctx.db and [m for m in range(1, 5)]), "total": 400_000}
        res = build_tools(ctx)["propose_meal"].execute(args)
        return TurnResult(final_text="", turn_id="t-g1", tools=[ToolInvocation("propose_meal", args, res)])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    spec = ProfileSpec.model_validate(k.store.get_version(k.seed_report["version_id"])["spec"]).model_copy(
        update={"prompt": k.default_spec.prompt.model_copy(update={"body": "CANDIDATE {{sender.name}}"})})
    world = evalhost.world_factory(case)
    try:
        result = await evalhost.run_turn(case, world, spec)
    finally:
        world.close()
    assert result.turn_id == "t-g1" and result.tools[0].result["bill_total"] == 400_000
    assert seen["system"].startswith("CANDIDATE An") and seen["today"] == "2026-07-20"   # the candidate prompt, the case's day


def test_start_eval_run_creates_the_row_and_spawns_the_job(api_client_room, monkeypatch):
    client, _headers, room_id, _m = api_client_room
    from app.db import get_db
    k = kernel_for(get_db())
    bid = k.seed_report["business_id"]
    r = client.post(f"/api/admin/businesses/{bid}/eval/import", headers=ADMIN)
    assert r.status_code == 200 and r.json()["suite"] == "lunch-typical"
    spawned = []
    monkeypatch.setattr(Kernel, "spawn", staticmethod(lambda argv: spawned.append(argv)))
    version = k.store.get_version(k.seed_report["version_id"])["version"]
    r = client.post(f"/api/admin/profiles/{k.seed_report['profile_id']}/versions/{version}/eval?suite=lunch-typical", headers=ADMIN)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "running" and run["profile_version_id"] == k.seed_report["version_id"]
    assert spawned[0][1:] == ["-m", "app.evalhost", "run", "--suite", "lunch-typical",
                              "--version", str(k.seed_report["version_id"]), "--run-id", str(run["id"])]
    assert client.get(f"/api/admin/eval/runs/{run['id']}", headers=ADMIN).json()["status"] == "running"
    assert client.post(f"/api/admin/profiles/{k.seed_report['profile_id']}/versions/{version}/eval?suite=nope",
                       headers=ADMIN).status_code == 404


async def test_eval_capture_writes_a_keyed_review_case_the_runner_skips(db, monkeypatch):
    room_id, m = _seed_room(db, 3)
    k = kernel_for(db)
    bid = k.seed_report["business_id"]
    d = k.store.create_draft(k.seed_report["profile_id"], actor="admin")
    pipeline = k.store.get_version(d["id"])["spec"]["pipeline"]
    pipeline["after"] = [{"id": "kernos.after.trace", "version": "1", "config": {}},
                         {"id": "kernos.after.eval_capture", "version": "1", "config": {"keep_days": 30}}]
    k.store.update_draft(d["id"], {"pipeline": pipeline}, actor="admin")
    k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="test")

    async def fake(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text="", turn_id="t-cap", tools=[
            ToolInvocation("find_members", {"names": ["M2"]}, {"ok": True}),
            ToolInvocation("propose_meal", {"participants": m, "payer": m[0], "total": 90_000,
                                            "adjustments": [{"member": m[1], "amount": 10_000}]},
                           {"ok": True, "payer_member_id": m[0], "member_participants": m, "bill_total": 90_000})])

    monkeypatch.setattr(agent_mod, "run_turn", fake)
    await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix tôi trả 90k")
    captured = k.store.list_cases(bid, source="captured")
    assert len(captured) == 1 and captured[0]["review"] is True and captured[0]["slug"] == f"cap-{room_id}-t-cap"
    case = k.store.get_case(bid, captured[0]["slug"])["case"]
    assert case["actor"] == f"m{m[0]}" and case["message"] == "@phoenix tôi trả 90k" and case["day"]
    assert case["expect"]["tools"] == ["propose_meal"]                       # find_members is scaffolding
    assert case["expect"]["args"]["propose_meal"] == {"participants": [f"m{x}" for x in m], "payer": f"m{m[0]}",
                                                      "total": 90_000, "adjustments": [{"member": f"m{m[1]}", "amount": 10_000}]}
    assert case["members"][0] == {"key": f"m{m[0]}", "display_name": "M1", "nickname": "m1"}
    assert all(set(mem) == {"key", "display_name", "nickname"} for mem in case["members"])    # no bank fields
    # the runner never grades it
    runner = Runner([], world_factory=lambda c: None, run_turn=None)
    out = await runner.run([EvalCase.from_dict(case)], spec=None)
    assert out["records"][0]["skipped"] == "review" and out["summary"]["skipped_review"] == 1

    # a prose-only turn captures nothing
    async def prose(user_text, ctx, images=None, emit=None, memory=None, history=None):
        return TurnResult(final_text="chào", turn_id="t-2")

    monkeypatch.setattr(agent_mod, "run_turn", prose)
    await chat.run_bot_turn(db, room_id, m[0], "M1", "@phoenix hi")
    assert len(k.store.list_cases(bid, source="captured")) == 1
