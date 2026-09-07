import pytest
from sqlalchemy import create_engine

from kernos.content import (
    Conflict, ContentStore, GateError, Invalid, Models, NotFound, PreconditionFailed, ProfileSpec,
    Runtime, bind, deep_merge, sessions_for,
)


class OkGates:
    def __init__(self): self.calls = []
    def check(self, spec, **kw):
        self.calls.append(kw); return []


class FailGates:
    def check(self, spec, **kw):
        from kernos.content import GateFailure
        return [GateFailure("money", "nope")]


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/c.db", future=True)
    bind(engine)
    return ContentStore(sessions_for(engine))


def _base_spec() -> dict:
    return ProfileSpec(models=Models(text="m"), runtime=Runtime(cwd="/c", agent_dir="/a"),
                       pipeline={}, meta={"handles_money": True}).stored()


@pytest.fixture
def seeded(store):
    b = store.create_business("lunch", "Lunch", seed={"spec": _base_spec()})
    store.put_source(b["id"], "rule", "money-safety", body="R1", actor="boot", frontmatter={"tags": ["money"]})
    store.put_source(b["id"], "skill", "record-meal", body="S1", actor="boot", frontmatter={"description": "d"})
    p = store.create_profile(b["id"], "default", managed_by="boot")
    v1 = store.create_draft(p["id"], actor="boot")
    store.publish(v1["id"], actor="boot", bypass_gates=True)
    a = store.create_agent(b["id"], "phoenix", "Phoenix", profile_id=p["id"], is_default=True)
    return store, b, p, v1, a


def test_sources_etag_precondition_and_delete(store):
    b = store.create_business("x", "X")
    s1 = store.put_source(b["id"], "rule", "r", body="one", actor="admin", title="T")
    assert len(s1["etag"]) == 32
    with pytest.raises(PreconditionFailed):
        store.put_source(b["id"], "rule", "r", body="two", actor="admin", if_match="stale")
    s2 = store.put_source(b["id"], "rule", "r", body="two", actor="admin", if_match=s1["etag"], title="T")
    assert s2["etag"] != s1["etag"]
    s3 = store.put_source(b["id"], "rule", "r", body="two", actor="admin", title="T2")   # title changes etag
    assert s3["etag"] != s2["etag"]
    with pytest.raises(Invalid):
        store.put_source(b["id"], "bogus", "r", body="", actor="admin")
    with pytest.raises(PreconditionFailed):
        store.delete_source(b["id"], "rule", "r", actor="admin", if_match="stale")
    store.delete_source(b["id"], "rule", "r", actor="admin", if_match=s3["etag"])
    with pytest.raises(NotFound):
        store.get_source(b["id"], "rule", "r")


def test_draft_snapshots_sources_and_a_later_edit_does_not_change_it(seeded):
    store, b, p, v1, _ = seeded
    spec = store.get_version(v1["id"])["spec"]
    assert spec["rules"] == [{"slug": "money-safety", "content": "R1", "tags": ["money"]}]
    assert spec["skills"][0] == {"name": "record-meal", "description": "d", "body": "S1", "delivery": "inline"}
    assert "runtime" not in spec                                       # never stored (finding 2)
    # an untouched seeded profile snapshots to itself (finding 1)
    d = store.create_draft(p["id"], actor="admin")
    assert store.get_version(d["id"])["spec"] == spec
    store.put_source(b["id"], "rule", "money-safety", body="R2", actor="admin", frontmatter={"tags": ["money"]})
    assert store.get_version(d["id"])["spec"]["rules"][0]["content"] == "R1"   # snapshot is stable
    d2 = store.create_draft(p["id"], actor="admin")
    assert store.get_version(d2["id"])["spec"]["rules"][0]["content"] == "R2"
    store.put_source(b["id"], "prompt", "system", body="You are {{persona.name}}", actor="admin")
    d3 = store.create_draft(p["id"], actor="admin")
    assert store.get_version(d3["id"])["spec"]["prompt"]["body"] == "You are {{persona.name}}"


def test_update_draft_deep_merges_validates_and_refuses_published(seeded):
    store, b, p, v1, _ = seeded
    d = store.create_draft(p["id"], actor="admin")
    out = store.update_draft(d["id"], {"models": {"thinking": "high"}, "caps": {"max_tools": 5}}, actor="admin")
    assert out["spec"]["models"] == {"text": "m", "vision": None, "thinking": "high", "thinking_budgets": None}
    assert out["spec"]["caps"] == {"max_tools": 5, "max_seconds": 120}
    with pytest.raises(Invalid):
        store.update_draft(d["id"], {"bogus": 1}, actor="admin")
    with pytest.raises(Invalid):
        store.update_draft(d["id"], {"runtime": {"cwd": "/tmp"}}, actor="admin")
    with pytest.raises(Conflict):
        store.update_draft(v1["id"], {"caps": {"max_tools": 1}}, actor="admin")
    assert deep_merge({"a": {"b": 1, "c": [1]}}, {"a": {"c": [2]}, "d": 3}) == {"a": {"b": 1, "c": [2]}, "d": 3}


def test_publish_flips_statuses_managed_by_and_audits(seeded):
    store, b, p, v1, _ = seeded
    assert store.get_profile(p["id"])["managed_by"] == "boot"
    d = store.create_draft(p["id"], actor="admin", note="try")
    gates = OkGates()
    with pytest.raises(Invalid):
        store.publish(d["id"], actor="admin")                          # gates required unless bypass
    out = store.publish(d["id"], actor="admin", gates=gates, override_reason="why")
    assert out["status"] == "published" and out["published_at"]
    assert gates.calls[0]["previous"] == store.get_version(v1["id"])["spec"]
    assert gates.calls[0]["override_reason"] == "why" and gates.calls[0]["actor"] == "admin"
    assert store.get_version(v1["id"])["status"] == "superseded"
    prof = store.get_profile(p["id"])
    assert prof["published_version_id"] == d["id"] and prof["managed_by"] == "human"
    with pytest.raises(Conflict):
        store.publish(d["id"], actor="admin", gates=gates)             # already published
    actions = [(a["action"], a["entity"]) for a in store.audit()]
    assert ("publish", "profile") in actions and ("create_version", "version") in actions


def test_gate_failure_blocks_publish_and_rollback_skips_probe(seeded):
    store, b, p, v1, _ = seeded
    d = store.create_draft(p["id"], actor="admin")
    with pytest.raises(GateError) as ei:
        store.publish(d["id"], actor="admin", gates=FailGates())
    assert ei.value.failures == [("money", "nope")]
    assert store.get_version(d["id"])["status"] == "draft"
    gates = OkGates()
    store.publish(d["id"], actor="admin", gates=gates)
    back = store.rollback(p["id"], 1, actor="admin", gates=gates)
    assert back["status"] == "published" and gates.calls[-1]["skip_probe"] is True
    assert store.get_version(d["id"])["status"] == "superseded"
    with pytest.raises(Conflict):
        store.rollback(p["id"], 1, actor="admin", gates=gates)         # it is published now
    with pytest.raises(Conflict):
        store.retire(back["id"], actor="admin")
    assert store.retire(d["id"], actor="admin")["status"] == "retired"


def test_agents_bindings_and_change_hook(seeded):
    store, b, p, v1, a = seeded
    fired = []
    store.on_change.append(lambda: fired.append(1))
    assert store.default_agent("lunch")["slug"] == "phoenix"
    sub = store.create_agent(b["id"], "helper", "Helper", profile_id=p["id"], role="sub")
    with pytest.raises(Invalid):
        store.bind_space("room-1", sub["id"])                           # a space binds to a manager
    with pytest.raises(Invalid):
        store.bind_space("room-1", a["id"], overrides={"bogus": 1})
    bnd = store.bind_space("room-1", a["id"], overrides={"append_sections": ["Speak English"]})
    assert bnd["overrides"]["append_sections"] == ["Speak English"] and fired == [1]
    assert store.get_binding("room-1")["agent_id"] == a["id"] and store.get_binding("room-2") is None
    store.unbind_space("room-1")
    assert store.get_binding("room-1") is None and len(fired) == 2
    with pytest.raises(NotFound):
        store.unbind_space("room-1")
    other = store.create_agent(b["id"], "other", "Other", profile_id=p["id"], is_default=True)
    assert store.default_agent(b["id"])["id"] == other["id"] and not store.get_agent(a["id"])["is_default"]


def test_catalogue(store):
    store.upsert_model("x/model", provider="openrouter", name="X", input=["text", "image"])
    assert store.get_model("x/model")["input"] == ["text", "image"] and store.get_model("nope") is None
    store.set_probe("x/model", {"ok": True, "checked_at": "2026-09-05T00:00:00+00:00"})
    assert store.list_models()[0]["probe"]["ok"] is True
    with pytest.raises(NotFound):
        store.set_probe("nope", {})
