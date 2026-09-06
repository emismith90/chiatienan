"""The steward: seeded on every boot, off until an operator connects it (plan Phase 10.2).

The two halves that matter are proved separately, because they are the whole risk of
shipping this to a live room: **off**, a room's manifest is the 19 tools it has always
had; **on**, the manager gains exactly one tool, and asking it produces a proposal
against the profile the steward reviews — which no agent can publish for itself.
"""
import pytest

from app import chat
from app.kernel import Kernel, kernel_for
from app.steward_profile import CAPABILITIES, SLUG, build_steward_spec
from kernos.content import Invalid
from kernos.osadmin import VERB_TOOLS
from tests.test_delegation import _install, _tool_names, _turn_done
from tests.test_ledger import _seed_room

READ_AND_DRAFT = set(VERB_TOOLS["read"]) | set(VERB_TOOLS["draft"])


def _steward(k):
    return next(a for a in k.store.list_agents(k.seed_report["business_id"]) if a["slug"] == SLUG)


async def _turn(monkeypatch, db, room_id, mm, text, *scripts):
    fake = _install(monkeypatch, *scripts)

    async def emit(e):
        pass
    reply = await chat.run_bot_turn(db, room_id, mm[0], "M1", text, emit=emit)
    return fake, reply


# ------------------------------------------------------------------------- seeded

def test_boot_seeds_a_sub_agent_with_its_own_profile_and_nothing_wired(db):
    k = kernel_for(db)
    lunch_pid = k.seed_report["profile_id"]
    steward = _steward(k)
    assert steward["role"] == "sub" and steward["is_default"] is False
    assert steward["capabilities"] == {**CAPABILITIES, "manages_profiles": [lunch_pid]}
    # the description a manager reads must not overclaim: it proposes, it does not change
    assert "changes nothing by itself" in steward["description"] and "never touches the ledger" in steward["description"]
    # its own profile, its own vocabulary: the CMS and nothing else
    assert steward["profile_id"] != lunch_pid
    spec = k.store.published_spec(steward["profile_id"])
    assert [t["pack"] for t in spec["tool_packs"]] == ["os_admin"]
    assert spec["builtin_tools"] == [] and spec["eval"]["suites"] == []
    assert "Steward brief" in spec["prompt"]["body"] and "cms_get_friction" in spec["prompt"]["body"]
    # nothing points at it: the room's agent is untouched
    assert k.store.default_agent(k.seed_report["business_id"])["delegates_to"] == []
    # and it cannot be granted publish — its profile names no eval suites (Phase 8 F3)
    with pytest.raises(Invalid, match="eval.suites"):
        k.store.update_agent(steward["id"], {"capabilities": {"cms": ["read", "publish"]}})


def test_reboot_is_a_no_op_and_never_takes_back_what_an_operator_changed(db):
    k = kernel_for(db)
    steward = _steward(k)
    assert "sub-agent steward created" in k.steward_report["actions"]
    granted = {"cms": ["read", "draft", "eval"], "manages_profiles": [k.seed_report["profile_id"]],
               "max_eval_runs_per_day": 1}
    k.store.update_agent(steward["id"], {"capabilities": granted})
    k.store.update_agent(k.store.default_agent(k.seed_report["business_id"])["id"],
                         {"delegates_to": [steward["id"]]})

    again = Kernel(db)                                        # a redeploy
    assert again.steward_report["actions"] == []
    assert _steward(again)["capabilities"] == granted         # the operator's grant survives
    assert again.store.default_agent(again.seed_report["business_id"])["delegates_to"] == [steward["id"]]


async def test_off_by_default_a_room_sees_the_same_nineteen_tools(db, monkeypatch):
    room_id, mm = _seed_room(db, 2)
    kernel_for(db)                                            # boots and seeds the steward
    fake, _ = await _turn(monkeypatch, db, room_id, mm, "@phoenix chào", [_turn_done("Chào.")])
    names = _tool_names(fake.runs[0])
    assert len(names) == 19 and not any(n.startswith(("ask_", "cms_")) for n in names)


# ---------------------------------------------------------------------------- on

def _switch_on(k):
    """The one admin call that connects the steward (README: PATCH /api/admin/agents/…)."""
    steward = _steward(k)
    k.store.update_agent(k.store.default_agent(k.seed_report["business_id"])["id"],
                         {"delegates_to": [steward["id"]]})
    return steward


async def test_asking_the_steward_drafts_against_the_profile_it_reviews_and_proposes_it(db, monkeypatch):
    room_id, mm = _seed_room(db, 2)
    k = kernel_for(db)
    lunch_pid = k.seed_report["profile_id"]
    steward = _switch_on(k)
    new_skill = "# record-meal\n\nAlways ask who paid before proposing."

    manager = [{"type": "tool_call", "call_id": "m1", "name": "ask_steward",
                "args": {"task": "review the last turns and fix what keeps going wrong"}},
               _turn_done("Đã nhờ steward xem lại.")]
    sub = [{"type": "tool_call", "call_id": "s1", "name": "cms_get_friction", "args": {}},
           {"type": "tool_call", "call_id": "s2", "name": "cms_draft_change",
            "args": {"kind": "skill", "slug": "record-meal", "body": new_skill, "profile_id": lunch_pid,
                     "rationale": "two turns guessed the payer"}},
           _turn_done("Drafted one change to record-meal.")]
    fake, reply = await _turn(monkeypatch, db, room_id, mm, "@phoenix nhờ steward xem lại", manager, sub)

    # the manager gained exactly one tool; the steward's own manifest is the CMS
    assert len(_tool_names(fake.runs[0])) == 20 and "ask_steward" in _tool_names(fake.runs[0])
    assert set(_tool_names(fake.runs[1])) == READ_AND_DRAFT           # read + draft, no eval, no publish
    assert "cms_publish" not in _tool_names(fake.runs[1])
    ask = next(t for t in fake.runs[0]["tools"] if t["name"] == "ask_steward")
    assert "changes nothing by itself" in ask["description"]

    # the draft landed on the LUNCH profile, authored by the steward, unpublished
    drafted = fake.tool_result("s2")
    assert drafted["ok"] and drafted["paths"] == ["skills"] and "+Always ask who paid" in drafted["diff"]
    version = k.store.get_version(drafted["version_id"])
    assert version["profile_id"] == lunch_pid and version["actor"] == "agent:steward" and version["status"] == "draft"
    assert k.store.get_profile(lunch_pid)["published_version_id"] != version["id"]
    assert reply.kind == "bot" and reply.body == "Đã nhờ steward xem lại."

    # a later turn proposes the draft it made earlier; a person approves it
    sub2 = [{"type": "tool_call", "call_id": "s3", "name": "cms_propose_publish",
             "args": {"version_id": drafted["version_id"], "rationale": "the payer was guessed twice"}},
            _turn_done("Proposed.")]
    fake2, _ = await _turn(monkeypatch, db, room_id, mm, "@phoenix gửi đề xuất đi",
                           [{"type": "tool_call", "call_id": "m2", "name": "ask_steward", "args": {"task": "propose it"}},
                            _turn_done("Đã gửi.")], sub2)
    proposed = fake2.tool_result("s3")
    assert proposed["ok"] and proposed["status"] == "pending"
    prop = k.store.get_proposal(proposed["proposal_id"])
    assert prop["profile_id"] == lunch_pid and prop["agent_id"] == steward["id"]
    assert [(c["kind"], c["slug"]) for c in prop["source_changes"]] == [("skill", "record-meal")]
    # the steward's reply to the manager names the proposal (the pack's Body, not its prose)
    assert f"Proposal #{prop['id']}" in fake2.tool_result("m2")["text"]

    out = k.approve_proposal(prop["id"], actor="hung")
    assert out["status"] == "approved"
    published = k.store.published_spec(lunch_pid)
    assert next(s for s in published["skills"] if s["name"] == "record-meal")["body"] == new_skill
    assert k.store.get_source(k.seed_report["business_id"], "skill", "record-meal")["body"] == new_skill


async def test_the_steward_can_never_publish_the_profile_it_reviews(db, monkeypatch):
    room_id, mm = _seed_room(db, 2)
    k = kernel_for(db)
    lunch_pid = k.seed_report["profile_id"]
    steward = _switch_on(k)
    # even handed the publish verb by force (the store refuses it; write the row directly)
    with k.store._session() as s:
        from kernos.content import models as m
        s.get(m.Agent, steward["id"]).capabilities = {"cms": ["read", "draft", "publish"],
                                                      "manages_profiles": [lunch_pid]}
    k.invalidate()
    sub = [{"type": "tool_call", "call_id": "s1", "name": "cms_draft_change",
            "args": {"kind": "prompt_append", "body": "Be brief.", "profile_id": lunch_pid, "rationale": "r"}},
           {"type": "tool_call", "call_id": "s2", "name": "cms_publish", "args": {"version_id": 0, "rationale": "r"}},
           {"type": "tool_call", "call_id": "s3", "name": "cms_draft_change",
            "args": {"kind": "rule", "slug": "money-safety", "body": "relaxed", "profile_id": lunch_pid, "rationale": "r"}},
           {"type": "tool_call", "call_id": "s4", "name": "cms_draft_change",
            "args": {"kind": "skill", "slug": "x", "body": "y", "profile_id": 9999, "rationale": "r"}},
           _turn_done("Tried.")]
    fake, _ = await _turn(monkeypatch, db, room_id, mm, "@phoenix steward",
                          [{"type": "tool_call", "call_id": "m1", "name": "ask_steward", "args": {"task": "t"}},
                           _turn_done("ok")], sub)
    drafted = fake.tool_result("s1")
    assert drafted["ok"]
    # cms_publish exists now, and refuses: a managed profile is a person's to approve
    refused = fake.tool_result("s2")
    assert refused["ok"] is False and "your own profile" in refused["error"]
    assert fake.tool_result("s3")["ok"] is False and "tagged money" in fake.tool_result("s3")["error"]
    assert fake.tool_result("s4")["ok"] is False and "profile_id must be" in fake.tool_result("s4")["error"]
    assert k.store.get_version(drafted["version_id"])["status"] == "draft"
    assert k.store.get_profile(lunch_pid)["published_version_id"] != drafted["version_id"]


def test_the_steward_spec_publishes_through_the_gates_unchanged(db):
    """It is seeded with `bypass_gates` like every boot profile; this proves it would
    also pass the gates on its own, so an operator editing it is not stuck."""
    k = kernel_for(db)
    failures = k.gates.check(build_steward_spec().stored(), previous=None, actor="admin")
    assert [f.as_tuple() for f in failures] == []
