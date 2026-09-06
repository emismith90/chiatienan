"""The CMS as a capability-gated tool pack (design §8; plan Task 8.2, review F1–F14)."""
import json

import pytest

from app import chat, drafts
from app.kernel import Kernel, kernel_for
from app.tools import ToolContext
from kernos.content import StaticResolver, ProfileSpec, ToolPackRef
from kernos.content import models as m
from kernos.eval import EvalCase, Runner, spec_sha
from kernos.osadmin import ALL_TOOLS, VERB_TOOLS, OsAdminPack, _redact
from tests.test_delegation import ScriptedBridge, _install, _tool_names, _turn_done
from tests.test_ledger import _seed_room

READ = set(VERB_TOOLS["read"])


def _enable(k, *, eval_suites=None, overrides=None):
    """Publish the lunch profile with `os_admin` enabled (and, boot-style, `eval.suites`)."""
    pid = k.seed_report["profile_id"]
    spec = k.store.published_spec(pid)
    packs = [*spec["tool_packs"], {"pack": "os_admin", "tools": overrides or {}}]
    d = k.store.create_draft(pid, actor="admin")
    patch = {"tool_packs": packs}
    if eval_suites is not None:
        patch["eval"] = {"suites": eval_suites}
    k.store.update_draft(d["id"], patch, actor="admin")
    if eval_suites is None and not overrides:
        return k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="test: enable os_admin")
    return k.store.publish(d["id"], actor="admin", bypass_gates=True)


def _grant(k, caps):
    agent = k.store.default_agent(k.seed_report["business_id"])
    return k.store.update_agent(agent["id"], {"capabilities": caps})


def _room(db, caps=None, **enable):
    room_id, mm = _seed_room(db, 2)
    k = kernel_for(db)
    _enable(k, **enable)
    if caps is not None:
        _grant(k, caps)
    return room_id, mm, k


async def _turn(monkeypatch, db, room_id, mm, text, *scripts):
    fake = _install(monkeypatch, *scripts)

    async def emit(e):
        pass
    reply = await chat.run_bot_turn(db, room_id, mm[0], "M1", text, emit=emit)
    return fake, reply


def _trace(k, room_id):
    return k.store.get_trace(str(room_id), k.store.list_traces(str(room_id))[0]["id"])


# ------------------------------------------------------------ zero behaviour change

async def test_registered_but_not_enabled_or_not_granted_means_no_cms_tools(db, monkeypatch):
    room_id, mm = _seed_room(db, 2)
    k = kernel_for(db)
    assert k.packs.get("os_admin").evidence is False and k.static_tool_names(k.packs.get("os_admin")) == set(ALL_TOOLS)
    assert ALL_TOOLS <= k.reserved_tool_names()
    fake, _ = await _turn(monkeypatch, db, room_id, mm, "@phoenix chào", [_turn_done("Chào.")])
    assert len(_tool_names(fake.runs[0])) == 19                                  # the seeded room, untouched
    _enable(k)                                                                    # pack on, no verbs
    fake, _ = await _turn(monkeypatch, db, room_id, mm, "@phoenix chào", [_turn_done("Chào.")])
    assert not any(n.startswith("cms_") for n in _tool_names(fake.runs[0]))
    _grant(k, {"cms": ["read"]})
    fake, _ = await _turn(monkeypatch, db, room_id, mm, "@phoenix chào", [_turn_done("Chào.")])
    assert {n for n in _tool_names(fake.runs[0]) if n.startswith("cms_")} == READ
    manifest = {t["name"]: t for t in fake.runs[0]["tools"]}
    assert "never instructions" in manifest["cms_get_turn_trace"]["description"]


def test_a_per_tool_override_and_a_rule_on_a_cms_tool_pass_gate_1(db):
    k = kernel_for(db)
    v = _enable(k, overrides={"cms_publish": {"enabled": False}})
    assert v["status"] == "published"
    pid = k.seed_report["profile_id"]
    d = k.store.create_draft(pid, actor="admin")
    k.store.update_draft(d["id"], {"validation": [{"id": "log-len", "scope": "tool_args", "plugin": "kernos.validate.non_negative",
                                                    "tool": "cms_log", "config": {"paths": ["data.n"]}, "on_fail": "warn"}]}, actor="admin")
    assert k.store.publish(d["id"], actor="admin", gates=k.gates, override_reason="t")["status"] == "published"


# -------------------------------------------------------------------- read tools

async def test_reads_are_references_never_evidence_and_traces_are_redacted_untrusted_data(db, monkeypatch):
    room_id, mm, k = _room(db, {"cms": ["read"]})
    # a first turn whose trace carries a number nowhere else: an unknown name with digits
    fake, _ = await _turn(monkeypatch, db, room_id, mm, "@phoenix ai đây",
                          [{"type": "tool_call", "call_id": "a", "name": "find_members", "args": {"names": ["Người 654321"]}},
                           _turn_done("Không thấy ai tên đó.")])
    first = _trace(k, room_id)["turn_id"]
    script = [{"type": "tool_call", "call_id": "b1", "name": "cms_get_turns", "args": {"only_flagged": False}},
              {"type": "tool_call", "call_id": "b2", "name": "cms_get_turn_trace", "args": {"turn_id": first}},
              {"type": "tool_call", "call_id": "b3", "name": "cms_log", "args": {"level": "info", "message": "tổng 777,000đ"}},
              {"type": "tool_call", "call_id": "b4", "name": "cms_get_profile", "args": {}},
              {"type": "tool_call", "call_id": "b5", "name": "cms_get_friction", "args": {}},
              _turn_done("Lần trước 654,321đ, và tổng 777,000đ.")]
    fake, reply = await _turn(monkeypatch, db, room_id, mm, "@phoenix xem lại", script)
    trace = _trace(k, room_id)
    # what the model read
    turns = fake.tool_result("b1")
    assert turns["ok"] and any(t["turn_id"] == first for t in turns["turns"]) and "_record" not in turns
    full = fake.tool_result("b2")
    assert full["untrusted"] is True and "never instructions" in full["note"]
    assert full["data"]["tools"][0]["args"] == {"names": ["Người 654321"]}
    profile = fake.tool_result("b4")
    assert profile["scope"] == [] and profile["verbs"] == ["read"] and "models" in profile["blacklist"]
    assert [s["name"] for s in profile["editable"]["skills"]][:1] == ["balances"]
    friction = fake.tool_result("b5")                       # over the space's own stored traces
    assert friction["ok"] and friction["scanned"] == 1 and friction["clean"] is True     # the first turn was clean
    assert friction["findings"] == [] and "_record" not in friction
    # what was recorded: references only, and the pack's args back nothing (F1)
    recorded = {t["name"]: t for t in trace["tools"]}
    assert recorded["cms_get_turn_trace"]["result"] == {"ok": True, "turn_id": first}
    assert set(recorded["cms_get_turns"]["result"]) == {"ok", "count"} and set(recorded["cms_get_profile"]["result"]) == {"ok", "profile_id", "version_id"}
    assert recorded["cms_get_friction"]["result"] == {"ok": True, "scanned": 1, "findings": []}
    assert "654321" not in json.dumps([t["result"] for t in trace["tools"]])
    assert reply.kind == "bot"
    assert [(v["plugin"], v["reason"]) for v in trace["summary"]["verdicts"]] == [
        ("app.validate.unbacked_amounts", "unbacked amounts [654321, 777000]")]
    assert trace["summary"]["agent_log"] == [{"level": "info", "message": "tổng 777,000đ", "data": None, "agent": "phoenix"}]
    # redaction
    assert _redact({"a": [{"qr_url": "x", "amount": 1, "to": {"bank_code": "970436", "account_number": "1", "name": "M"}}]}) == \
        {"a": [{"amount": 1, "to": {"name": "M"}}]}


# ------------------------------------------------------------------ draft/propose

async def test_draft_change_opens_one_own_draft_and_a_proposal_ends_the_turn_as_a_body(db, monkeypatch):
    room_id, mm, k = _room(db, {"cms": ["read", "draft"]})
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    k.store.put_source(bid, "skill", "balances", body="drifted, unpublished", actor="admin")      # snapshot noise (F6)
    script = [
        {"type": "tool_call", "call_id": "d1", "name": "cms_draft_change",
         "args": {"kind": "skill", "slug": "record-meal", "body": "# record-meal\n\nAlways ask who paid.", "rationale": "3 turns guessed the payer"}},
        {"type": "tool_call", "call_id": "d2", "name": "cms_draft_change",
         "args": {"kind": "prompt_append", "body": "Ask before assuming the payer.", "rationale": "same"}},
        {"type": "tool_call", "call_id": "d3", "name": "cms_draft_change",
         "args": {"kind": "rule", "slug": "money-safety", "body": "relaxed", "rationale": "x"}},
        {"type": "tool_call", "call_id": "d4", "name": "cms_draft_change",
         "args": {"kind": "rule", "slug": "new-rule", "body": "r", "rationale": "x", "frontmatter": {"tags": ["money"]}}},
        {"type": "tool_call", "call_id": "d5", "name": "cms_propose_publish", "args": {"version_id": 0, "rationale": "x"}},
        {"type": "tool_call", "call_id": "d6", "name": "cms_publish", "args": {"version_id": 0, "rationale": "x"}},
        _turn_done("Đã đề xuất."),
    ]
    # the proposal names the draft: patch the script's version id once the draft exists
    fake = ScriptedBridge(script)
    monkeypatch.setattr("app.pi_bridge.get_bridge", lambda: fake)
    original = fake.request

    async def request(command):
        async for message in original(command):
            if message.get("call_id") in ("d5", "d6") and fake.sent:
                d1 = json.loads(next(s["content"] for s in fake.sent if s["call_id"] == "d1"))
                message = dict(message, args={**message["args"], "version_id": d1["version_id"]})
            yield message
    fake.request = request

    async def emit(e):
        pass
    reply = await chat.run_bot_turn(db, room_id, mm[0], "M1", "@phoenix sửa skill", emit=emit)
    d1, d2 = fake.tool_result("d1"), fake.tool_result("d2")
    assert d1["ok"] and d1["version_id"] == d2["version_id"] and d1["version"] == d2["version"]    # one draft per turn
    assert d1["paths"] == ["skills"] and sorted(d2["paths"]) == ["prompt.append", "skills"]      # no drift noise (F6)
    assert "+Always ask who paid." in d1["diff"] and "Ask before assuming" in d2["diff"]
    assert fake.tool_result("d3")["ok"] is False and "tagged money" in fake.tool_result("d3")["error"]
    assert fake.tool_result("d4")["ok"] is False and "tagged money" in fake.tool_result("d4")["error"]
    version = k.store.get_version(d1["version_id"])
    assert version["actor"] == "agent:phoenix" and version["status"] == "draft"
    assert next(s for s in version["spec"]["skills"] if s["name"] == "balances")["body"] != "drifted, unpublished"
    prop = fake.tool_result("d5")
    assert prop["ok"] and prop["status"] == "pending" and sorted(prop["paths"]) == ["prompt.append", "skills"]
    assert fake.tool_result("d6") == {"ok": False, "error": fake.tool_result("d6")["error"]} and "cms_publish" not in _tool_names(fake.runs[0])
    row = k.store.get_proposal(prop["proposal_id"])
    assert [(c["kind"], c["slug"]) for c in row["source_changes"]] == [("skill", "record-meal")]
    assert row["source_changes"][0]["if_match"] == k.store.get_source(bid, "skill", "record-meal")["etag"]
    assert row["base_version_id"] == k.store.get_profile(pid)["published_version_id"]
    # the reply is a body naming the proposal, not a card (F4)
    assert reply.kind == "bot" and reply.body.startswith(f"📋 Proposal #{prop['proposal_id']} opened for v{d1['version']}")
    assert f"/api/admin/proposals/{prop['proposal_id']}" in reply.body
    with db.session() as s:
        assert drafts.list_pending_drafts(s, room_id) == []
    trace = _trace(k, room_id)
    assert {t["name"]: t["result"] for t in trace["tools"]}["cms_propose_publish"] == {"ok": True, "proposal_id": prop["proposal_id"]}
    # a human approves: the version publishes and the skill source follows
    out = k.approve_proposal(prop["proposal_id"], actor="hung")
    assert out["status"] == "approved"
    assert k.store.published_spec(pid)["prompt"]["append"][-1] == "Ask before assuming the payer."
    assert k.store.get_source(bid, "skill", "record-meal")["body"].endswith("Always ask who paid.")
    fresh = k.store.create_draft(pid, actor="admin")
    assert next(s for s in k.store.get_version(fresh["id"])["spec"]["skills"] if s["name"] == "record-meal")["body"].endswith("who paid.")


# ------------------------------------------------------------------- self-publish

def _mini_suite(k, bid):
    from app import evalhost
    case = EvalCase(id="G1", source="t", day="2026-07-20", actor="m1", message="@bot x", expect={"tools": ["find_members"]})
    k.store.put_case(bid, "G1", case.to_dict(), actor="admin")
    return k.store.put_suite(bid, "mini", actor="admin", case_slugs=["G1"], graders=evalhost.LUNCH_GRADERS[:1])


def _finished_run(k, suite, version_id, *, agent_id=None):
    sha = spec_sha(ProfileSpec.model_validate(k.store.get_version(version_id)["spec"]))
    run = k.store.create_run(suite["id"], version_id, sha, actor="t", agent_id=agent_id)
    return k.store.finish_run(run["id"], status="done", summary={"graders": [{"name": "tool_selection", "blocking": True, "passed": 1, "failed": 0, "rate": 1.0}]})


def _agent_ctx(k, room_id):
    agent = k.store.default_agent(k.seed_report["business_id"])
    from kernos.kernel import Principal, TurnContext
    spec = k.resolve(room_id)
    ctx = ToolContext(db=k.db, room_id=room_id, sender_member_id=1, agent=agent)
    ctx.turn = TurnContext(space_id=str(room_id), principal=Principal(1, "M1"), text="x", profile=spec, tool_ctx=ctx,
                           extras={"agent": agent})
    return ctx, agent


def test_self_publish_needs_scope_blacklist_evidence_and_ownership(db, monkeypatch):
    room_id, mm, k = _room(db, None, eval_suites=["mini"])
    bid, pid = k.seed_report["business_id"], k.seed_report["profile_id"]
    suite = _mini_suite(k, bid)
    _grant(k, {"cms": ["read", "draft", "eval", "publish"], "self_change_scope": ["prompt.append"]})
    ctx, agent = _agent_ctx(k, room_id)
    tools = k.packs.get("os_admin").tools(ctx)
    assert set(tools) == ALL_TOOLS
    # a prompt append: inside scope, but no eval evidence yet
    d = tools["cms_draft_change"].execute({"kind": "prompt_append", "body": "Be brief.", "rationale": "r"})
    refused = tools["cms_publish"].execute({"version_id": d["version_id"], "rationale": "r"})
    assert refused["ok"] is False and "no finished run" in refused["error"]
    # with a finished run of exactly this content it publishes itself
    _finished_run(k, suite, d["version_id"], agent_id=agent["id"])
    spawned = []
    monkeypatch.setattr(Kernel, "spawn", staticmethod(lambda argv: spawned.append(argv)))
    out = tools["cms_publish"].execute({"version_id": d["version_id"], "rationale": "shorter replies"})
    assert out["ok"] and out["published"] and out["paths"] == ["prompt.append"], out
    assert k.store.published_spec(pid)["prompt"]["append"][-1] == "Be brief."
    prop = k.store.get_proposal(out["proposal_id"])
    assert prop["status"] == "auto_published" and prop["decided_by"] == "agent:phoenix" and prop["source_changes"] == []
    publish_audit = [a for a in k.store.audit() if a["action"] == "publish"][0]
    assert publish_audit["actor"] == "agent:phoenix" and "self-publish" in publish_audit["after"]["override_reason"]
    assert out["_record"] == {"ok": True, "version_id": d["version_id"], "version": d["version"], "proposal_id": prop["id"]}
    # a skill change is outside the scope; a blacklisted change is refused before the gates
    ctx.turn.extras.pop("cms_draft")
    d2 = tools["cms_draft_change"].execute({"kind": "skill", "slug": "balances", "body": "new", "rationale": "r"})
    _finished_run(k, suite, d2["version_id"])
    refused = tools["cms_publish"].execute({"version_id": d2["version_id"], "rationale": "r"})
    assert refused["ok"] is False and "outside your self-change scope" in refused["error"] and "skills" in refused["error"]
    v3 = k.store.create_draft(pid, actor="agent:phoenix", snapshot=False)
    k.store.update_draft(v3["id"], {"models": {**k.store.published_spec(pid)["models"], "text": "other"}}, actor="agent:phoenix")
    refused = tools["cms_publish"].execute({"version_id": v3["id"], "rationale": "r"})
    assert refused["ok"] is False and "blacklisted" in refused["error"] and "models" in refused["error"]
    # not the agent's own draft
    human = k.store.create_draft(pid, actor="admin")
    assert "you created" in tools["cms_publish"].execute({"version_id": human["id"], "rationale": "r"})["error"]
    assert "you created" in tools["cms_propose_publish"].execute({"version_id": human["id"], "rationale": "r"})["error"]
    assert k.store.get_version(v3["id"])["status"] == "draft" and k.store.get_version(d2["version_id"])["status"] == "draft"


def test_eval_runs_are_jobs_bounded_per_day_and_absent_in_eval_mode(db, monkeypatch):
    room_id, mm, k = _room(db, None, eval_suites=["mini"])
    bid = k.seed_report["business_id"]
    suite = _mini_suite(k, bid)
    _grant(k, {"cms": ["read", "draft", "eval"], "max_eval_runs_per_day": 2})
    ctx, agent = _agent_ctx(k, room_id)
    tools = k.packs.get("os_admin").tools(ctx)
    assert "cms_publish" not in tools and "cms_run_eval" in tools
    spawned = []
    monkeypatch.setattr(Kernel, "spawn", staticmethod(lambda argv: spawned.append(argv)))
    d = tools["cms_draft_change"].execute({"kind": "prompt_append", "body": "x", "rationale": "r"})
    first = tools["cms_run_eval"].execute({"suite": "mini", "version_id": d["version_id"]})
    assert first["ok"] and first["status"] == "running" and len(spawned) == 1 and first["_record"] == {"ok": True, "run_id": first["run_id"]}
    assert k.store.get_run(first["run_id"])["agent_id"] == agent["id"]
    # a fresh running run blocks; a finished one does not; the cap is per day
    blocked = tools["cms_run_eval"].execute({"suite": "mini", "version_id": d["version_id"]})
    assert blocked["ok"] is False and "already running" in blocked["error"]
    k.store.finish_run(first["run_id"], status="done", summary={"graders": []})
    second = tools["cms_run_eval"].execute({"suite": "mini", "version_id": d["version_id"]})
    assert second["ok"]
    k.store.finish_run(second["run_id"], status="failed", error="boom")
    third = tools["cms_run_eval"].execute({"suite": "mini", "version_id": d["version_id"]})
    assert third["ok"] is False and "daily eval budget" in third["error"] and len(spawned) == 2
    # a stale running run (older than 30 min) does not block
    _grant(k, {"cms": ["read", "draft", "eval"], "max_eval_runs_per_day": 5})
    ctx, agent = _agent_ctx(k, room_id)
    tools = k.packs.get("os_admin").tools(ctx)
    stale = k.store.create_run(suite["id"], d["version_id"], "sha", actor="t", agent_id=agent["id"])
    with k.store._session() as s:
        s.get(m.EvalRun, stale["id"]).started = "2020-01-01T00:00:00+00:00"
    fourth = tools["cms_run_eval"].execute({"suite": "mini", "version_id": d["version_id"]})
    assert fourth["ok"], fourth
    results = tools["cms_get_eval_results"].execute({"suite": "mini"})
    by_id = {r["run_id"]: r for r in results["suites"][0]["runs"]}
    assert by_id[stale["id"]]["status"] == "stale" and by_id[first["run_id"]]["status"] == "done"
    assert by_id[second["run_id"]]["status"] == "failed" and by_id[second["run_id"]]["error"] == "boom"
    assert results["_record"] == {"ok": True, "suites": ["mini"]}
    assert tools["cms_get_eval_results"].execute({"suite": "nope"})["ok"] is False
    # a review case the runner skips
    case = tools["cms_add_eval_case"].execute({"message": "@phoenix ghi 300k", "expect": {"tools": ["propose_meal"]}, "tags": ["payer"]})
    row = k.store.get_case(bid, case["case"])
    assert row["review"] is True and row["source"] == "agent" and row["case"]["message"] == "@phoenix ghi 300k"
    runner = Runner([], world_factory=lambda c: None, run_turn=None)
    import asyncio
    rec = asyncio.run(runner.run_one(EvalCase.from_dict(row["case"]), 0, None))
    assert rec["skipped"] == "review"
    assert tools["cms_add_eval_case"].execute({"message": "x", "expect": {}})["ok"] is False
    # eval mode: read tools only, nothing to spawn
    ek = Kernel(db, resolver=StaticResolver(k.resolve(room_id)), eval_mode=True)
    ectx = ToolContext(db=db, room_id=room_id, agent=agent)
    assert set(ek.packs.get("os_admin").tools(ectx)) == READ


def test_the_steward_brief_is_served(api_client_room):
    client, _h, _r, _m = api_client_room
    r = client.get("/api/admin/steward/brief", headers={"X-Admin-Password": "test-admin-pw"})
    assert r.status_code == 200 and r.json()["brief"].startswith("# Steward brief") and "cms_publish" in r.json()["brief"]
