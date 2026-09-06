from datetime import datetime, timezone

import pytest

from kernos.content import GateFailure, Models, PipelineEntry, ProfileSpec, PublishGates, Rule, blacklisted_changes
from kernos.kernel import BasePlugin, Stage
from kernos.registry import Registry


class P(BasePlugin):
    def __init__(self, id_, stage, money=False):
        self.id, self.stage, self.handles_money = id_, Stage(stage), money
        self.config_schema = {"type": "object", "additionalProperties": False, "properties": {}}
    async def run(self, ctx, config): return None


class Catalogue:
    def __init__(self, rows): self.rows = rows
    def get_model(self, model_id): return self.rows.get(model_id)


class Clock:
    def now(self): return datetime(2026, 9, 5, tzinfo=timezone.utc)


def _registry():
    r = Registry()
    r.register_all([P("k.model", "model"), P("k.run", "run"), P("k.render", "render"), P("k.money", "validate", money=True)])
    return r


PIPE = {"model": [PipelineEntry(id="k.model", version="1")], "run": [PipelineEntry(id="k.run", version="1")],
        "render": [PipelineEntry(id="k.render", version="1")]}
CAT = Catalogue({"good": {"probe": {"ok": True, "checked_at": "2026-08-20T00:00:00+00:00"}},
                 "stale": {"probe": {"ok": True, "checked_at": "2026-06-01T00:00:00+00:00"}},
                 "failed": {"probe": {"ok": False, "checked_at": "2026-09-01T00:00:00+00:00"}}})


def _gates(**kw):
    return PublishGates(_registry(), CAT, clock=Clock(), **kw)


def _spec(**kw):
    base = dict(models=Models(text="good"), pipeline=PIPE)
    base.update(kw)
    return ProfileSpec(**base)


def _names(failures): return [f.gate for f in failures]


def test_gate1_schema_pipeline_and_discoverable_skills():
    bad = {"models": {"text": "good"}, "pipeline": {"model": [{"id": "k.nope", "version": "1"}]}}
    fails = _gates().check(bad, previous=None, actor="admin")
    assert _names(fails) == ["schema"] and "k.nope" in fails[0].message
    assert _gates().check({"models": {"text": "good"}, "bogus": 1}, previous=None, actor="admin")[0].gate == "schema"
    from kernos.content import Skill
    spec = _spec(skills=[Skill(name="s", delivery="discoverable")])
    assert any("discoverable" in f.message for f in _gates().check(spec, previous=None, actor="admin"))


def test_gate2_money_needs_override_when_bash_is_on():
    spec = _spec(builtin_tools=["read", "bash"], meta={"handles_money": True})
    fails = _gates().check(spec, previous=None, actor="admin")
    assert _names(fails) == ["money"]
    assert _gates().check(spec, previous=None, actor="admin", override_reason="benchmarked") == []
    # plugin flag also counts
    piped = _spec(builtin_tools=["bash"], pipeline={**PIPE, "validate": [PipelineEntry(id="k.money", version="1")]})
    assert _names(_gates().check(piped, previous=None, actor="admin")) == ["money"]
    assert _gates().check(_spec(builtin_tools=["bash"]), previous=None, actor="admin") == []   # not money


def test_gate3_probe_only_for_changed_models():
    prev = _spec(models=Models(text="stale")).model_dump()
    assert _gates().check(_spec(models=Models(text="stale")), previous=prev, actor="admin") == []   # unchanged
    for bad, word in (("stale", "older"), ("failed", "no passing"), ("unknown", "no passing")):
        fails = _gates().check(_spec(models=Models(text=bad)), previous=None, actor="admin")
        assert _names(fails) == ["probe"] and word in fails[0].message, (bad, fails)
    assert _gates().check(_spec(models=Models(text="good", vision="good")), previous=None, actor="admin") == []
    assert _gates().check(_spec(models=Models(text="stale")), previous=None, actor="admin", skip_probe=True) == []


def test_gate5_reflexivity_blocks_agents_on_blacklisted_paths_only():
    prev = _spec(rules=[Rule(slug="money-safety", content="R", tags=["money"])], meta={"handles_money": True})
    same_prompt = prev.model_copy(update={"prompt": prev.prompt.model_copy(update={"body": "new"})})
    assert _gates().check(same_prompt, previous=prev, actor="agent:phoenix") == []
    for change in (
        {"builtin_tools": ["bash"]}, {"caps": prev.caps.model_copy(update={"max_seconds": 999})},
        {"settings": {"shellPath": "/bin/sh"}}, {"extensions": [{"id": "x"}]},
        {"rules": [Rule(slug="money-safety", content="weaker", tags=["money"])]}, {"meta": {}},
    ):
        spec = prev.model_copy(update=change)
        fails = [f for f in _gates().check(spec, previous=prev, actor="agent:phoenix", override_reason="x")
                 if f.gate == "reflexivity"]
        assert fails, change
    # a human may make the same change
    assert not [f for f in _gates().check(prev.model_copy(update={"caps": prev.caps.model_copy(update={"max_seconds": 999})}),
                                          previous=prev, actor="admin") if f.gate == "reflexivity"]
    assert "meta.handles_money" in blacklisted_changes(prev, prev.model_copy(update={"meta": {}}))
    assert blacklisted_changes(prev, prev) == []


def test_eval_gate_hook_is_called_with_the_version_and_skipped_on_rollback():
    seen = {}

    def hook(spec, *, profile_id, version_id):
        seen.update(profile_id=profile_id, version_id=version_id)
        return [GateFailure("eval", "ledger_state dropped")]

    g = _gates(eval_gate=hook)
    assert _names(g.check(_spec(), previous=None, actor="admin", profile_id=3, version_id=9)) == ["eval"]
    assert seen == {"profile_id": 3, "version_id": 9}
    assert _names(g.check(_spec(), previous=None, actor="admin", skip_eval=True)) == []
