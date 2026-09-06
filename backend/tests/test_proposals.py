"""Change proposals and their approval (design §8.4; plan Task 8.1, review F2/F6/F10/F11)."""
import pytest

from app import evalhost
from app.agent import TurnResult
from app.kernel import Kernel, kernel_for
from kernos.content import Conflict, Invalid, PreconditionFailed, ProfileSpec, ensure_seeded
from kernos.content.errors import GateError
from tests.test_evalhost import _import, _mini_suite

ADMIN = {"X-Admin-Password": "test-admin-pw"}


def _agent_draft(k, patch):
    """What the os_admin pack will do: a byte-identical draft of the published version,
    patched, as the agent."""
    pid = k.seed_report["profile_id"]
    d = k.store.create_draft(pid, actor="agent:phoenix", snapshot=False)
    return k.store.update_draft(d["id"], patch, actor="agent:phoenix")


def _skill_change(k, bid, slug, body):
    src = k.store.get_source(bid, "skill", slug)
    return {"kind": "skill", "slug": slug, "body": body, "title": src["title"],
            "frontmatter": dict(src["frontmatter"]), "if_match": src["etag"]}


def _propose(k, version, **kw):
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    agent = k.store.default_agent(bid)
    return k.store.create_proposal(bid, agent["id"], pid, version["id"], rationale=kw.pop("rationale", "r"),
                                   diff=kw.pop("diff", {"paths": ["skills"]}), actor="agent:phoenix", **kw)


def test_snapshot_false_gives_a_byte_identical_base_and_the_snapshot_drift_case(db):
    k = kernel_for(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    published = k.store.published_spec(pid)
    k.store.put_source(bid, "skill", "balances", body="drifted", actor="admin")           # a source edit not yet published
    plain = k.store.create_draft(pid, actor="admin")
    assert k.store.get_version(plain["id"])["spec"] != published                          # the snapshot picks it up…
    agent = k.store.create_draft(pid, actor="agent:phoenix", snapshot=False)
    assert k.store.get_version(agent["id"])["spec"] == published                          # …the agent's base does not


def test_a_human_approves_a_proposal_that_the_agent_could_never_publish(db):
    k = kernel_for(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    published_models = k.store.published_spec(pid)["models"]
    vision = published_models["vision"]
    new_skill = "# record-meal\n\nAlways ask who paid."
    v = _agent_draft(k, {"models": {**published_models, "vision": None},
                         "skills": [dict(s, body=new_skill) if s["name"] == "record-meal" else s
                                    for s in k.store.published_spec(pid)["skills"]]})
    prop = _propose(k, v, source_changes=[_skill_change(k, bid, "record-meal", new_skill)], base_version_id=1)
    assert prop["status"] == "pending" and prop["base_version_id"] == 1
    # the agent itself may not publish this (models is blacklisted) — gate 5
    with pytest.raises(GateError) as exc:
        k.store.publish(v["id"], actor="agent:phoenix", gates=k.gates)
    assert any(g == "reflexivity" and "models" in m for g, m in exc.value.failures)
    # an agent may not decide it either
    with pytest.raises(Invalid, match="non-agent"):
        k.approve_proposal(prop["id"], actor="agent:steward")
    out = k.approve_proposal(prop["id"], actor="hung")
    assert out["status"] == "approved" and out["decided_by"] == "hung" and out["last_error"] is None
    assert k.store.get_version(v["id"])["status"] == "published"
    assert k.store.published_spec(pid)["models"]["vision"] is None and vision is not None
    src = k.store.get_source(bid, "skill", "record-meal")
    assert src["body"] == new_skill and src["updated_by"] == "agent:phoenix"
    assert src["frontmatter"]["audit"] == {"proposal": prop["id"], "approved_by": "hung"}
    # the source follows: a fresh (snapshotting) draft keeps the change…
    fresh = k.store.create_draft(pid, actor="admin")
    assert next(s for s in k.store.get_version(fresh["id"])["spec"]["skills"] if s["name"] == "record-meal")["body"] == new_skill
    # …and boot does not revert an edited source (F2), while it still re-puts its own
    from app.kernel import default_sources
    from app.config import settings
    report = ensure_seeded(k.store, business_slug="lunch", business_name="Lunch ledger", spec=k.default_spec,
                           agent_slug="phoenix", agent_name="Phoenix", sources=default_sources())
    assert not any("record-meal" in a for a in report["actions"])
    assert k.store.get_source(bid, "skill", "record-meal")["body"] == new_skill
    assert k.store.get_proposal(prop["id"])["status"] == "approved"
    with pytest.raises(Conflict):
        k.approve_proposal(prop["id"], actor="hung")
    audit = [a for a in k.store.audit() if a["entity"] == "proposal"]
    assert {a["action"] for a in audit} == {"propose", "decide"} and audit[0]["actor"] == "hung"


def test_a_failed_gate_or_a_stale_source_leaves_the_proposal_pending_with_the_error(db):
    k = kernel_for(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    v = _agent_draft(k, {"models": {**k.store.published_spec(pid)["models"], "text": "nobody/unprobed"}})
    prop = _propose(k, v)
    with pytest.raises(GateError):
        k.approve_proposal(prop["id"], actor="hung")
    again = k.store.get_proposal(prop["id"])
    assert again["status"] == "pending" and "probe" in again["last_error"] and again["decided_by"] is None
    # a source edited since the draft: nothing is published
    v2 = _agent_draft(k, {"prompt": {"append": ["Be brief."]}})
    change = _skill_change(k, bid, "balances", "new balances skill")
    prop2 = _propose(k, v2, source_changes=[change])
    k.store.put_source(bid, "skill", "balances", body="someone else edited this", actor="admin")
    with pytest.raises(PreconditionFailed):
        k.approve_proposal(prop2["id"], actor="hung")
    assert k.store.get_proposal(prop2["id"])["status"] == "pending"
    assert k.store.get_version(v2["id"])["status"] == "draft"
    assert k.store.get_source(bid, "skill", "balances")["body"] == "someone else edited this"
    # reject retires the draft
    out = k.reject_proposal(prop2["id"], actor="hung")
    assert out["status"] == "rejected" and k.store.get_version(v2["id"])["status"] == "retired"
    with pytest.raises(Conflict):
        k.reject_proposal(prop2["id"], actor="hung")
    with pytest.raises(Invalid):
        k.store.decide_proposal(prop2["id"], status="maybe", by="x", actor="x")


def test_the_publish_verb_needs_eval_suites_on_the_published_profile(db):
    k = kernel_for(db)
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    agent = k.store.default_agent(bid)
    with pytest.raises(Invalid, match="eval.suites"):
        k.store.update_agent(agent["id"], {"capabilities": {"cms": ["read", "publish"]}})
    with pytest.raises(Invalid, match="eval.suites"):
        k.store.create_agent(bid, "auditor", "Auditor", profile_id=pid, capabilities={"cms": ["publish"]})
    with pytest.raises(Invalid, match="capabilities.cms"):
        k.store.update_agent(agent["id"], {"capabilities": {"cms": ["root"]}})
    ok = k.store.update_agent(agent["id"], {"capabilities": {"cms": ["eval", "read"], "self_change_scope": ["prompt.append"]}})
    assert ok["capabilities"] == {"cms": ["read", "eval"], "self_change_scope": ["prompt.append"]}
    # with eval.suites published (boot-style, gates bypassed) the grant is allowed
    d = k.store.create_draft(pid, actor="admin")
    k.store.update_draft(d["id"], {"eval": {"suites": ["mini"]}}, actor="admin")
    k.store.publish(d["id"], actor="admin", bypass_gates=True)
    assert "publish" in k.store.update_agent(agent["id"], {"capabilities": {"cms": ["publish"]}})["capabilities"]["cms"]


def test_proposal_routes(api_client_room):
    client, _headers, _room_id, _m = api_client_room
    from app.db import get_db
    k = kernel_for(get_db())
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    assert client.get("/api/admin/proposals").status_code == 401
    assert client.get("/api/admin/proposals", headers=ADMIN).json() == []
    v = _agent_draft(k, {"prompt": {"append": ["Be brief."]}})
    prop = _propose(k, v)
    rows = client.get(f"/api/admin/proposals?business_id={bid}&status=pending", headers=ADMIN).json()
    assert [r["id"] for r in rows] == [prop["id"]]
    assert client.get(f"/api/admin/proposals/{prop['id']}", headers=ADMIN).json()["version_id"] == v["id"]
    assert client.get("/api/admin/proposals/999", headers=ADMIN).status_code == 404
    r = client.post(f"/api/admin/proposals/{prop['id']}/approve", headers={**ADMIN, "X-Actor": "agent:x"})
    assert r.status_code == 422 and "non-agent" in r.text
    r = client.post(f"/api/admin/proposals/{prop['id']}/approve", headers={**ADMIN, "X-Actor": "hung"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert k.store.published_spec(pid)["prompt"]["append"] == ["Be brief."]
    v2 = _agent_draft(k, {"prompt": {"append": ["Be terse."]}})
    prop2 = _propose(k, v2)
    assert client.post(f"/api/admin/proposals/{prop2['id']}/reject", headers=ADMIN).json()["status"] == "rejected"
    assert client.post(f"/api/admin/proposals/{prop2['id']}/reject", headers=ADMIN).status_code == 409


async def test_eval_runs_carry_the_agent_and_the_eval_host_hands_it_to_the_turn(db, monkeypatch):
    k, bid, _ = _import(db)
    _mini_suite(k, bid, ["G1"])
    agent = k.store.default_agent(bid)
    seen = []

    async def fake(case, world, spec, *, agent=None):
        seen.append(agent)
        return TurnResult(final_text="", turn_id="t")

    v = k.store.get_version(k.seed_report["version_id"])
    run = await evalhost.run_suite(k, "mini", v["id"], run_turn=fake, world_factory=evalhost.world_factory, agent_id=agent["id"])
    assert run["agent_id"] == agent["id"] and seen == [agent]
    assert k.store.agent_runs_since(bid, "2000-01-01T00:00:00+00:00")[0]["id"] == run["id"]
    assert k.store.agent_runs_since(bid, "2999-01-01T00:00:00+00:00") == []
    # start_eval_run carries it to the row and the spawned job; refused in eval mode
    spawned = []
    monkeypatch.setattr(Kernel, "spawn", staticmethod(lambda argv: spawned.append(argv)))
    started = k.start_eval_run("mini", v["id"], actor="admin", agent_id=agent["id"])
    assert started["agent_id"] == agent["id"] and spawned and "--run-id" in spawned[0]
    with pytest.raises(Invalid):
        Kernel(db, eval_mode=True).start_eval_run("mini", v["id"], actor="admin")
    # the default run_turn puts the agent on the turn's tool context and extras
    import app.agent as agent_mod
    from tests.test_evalhost import _oracle  # noqa: F401  (same world helpers)
    got = {}

    async def fake_run_turn(user_text, ctx, images=None, emit=None, memory=None, history=None):
        got["agent"] = ctx.agent
        return TurnResult(final_text="ok", turn_id="t1")

    monkeypatch.setattr(agent_mod, "run_turn", fake_run_turn)
    from kernos.eval import EvalCase
    case = EvalCase.from_dict(k.store.get_case(bid, "G1")["case"])
    world = evalhost.EvalWorld(case, evalhost.fixtures_for(ProfileSpec.model_validate(v["spec"]), k))
    try:
        await evalhost.run_turn(case, world, ProfileSpec.model_validate(v["spec"]).with_runtime(k.default_spec.runtime), agent=agent)
    finally:
        world.close()
    assert got["agent"] == agent
