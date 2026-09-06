import pytest

from kernos.kernel import BasePlugin, PipelineError, PluginRef, Stage
from kernos.registry import ConfigError, Registry, RegistryError, schema_hash


class P(BasePlugin):
    def __init__(self, id_, version="1", stage="context", schema=None):
        self.id, self.version, self.stage = id_, version, Stage(stage)
        self.config_schema = schema or {"type": "object", "additionalProperties": False}

    async def run(self, ctx, config):
        return None


WINDOW = {"type": "object", "properties": {"weeks": {"type": "integer", "minimum": 1}},
          "required": ["weeks"], "additionalProperties": False}


def _reg():
    r = Registry()
    r.register(P("k.mem", schema=WINDOW))
    r.register(P("k.model", stage="model"))
    r.register(P("k.run", stage="run"))
    r.register(P("k.render", stage="render"))
    return r


def test_get_requires_a_version_and_names_known_ones():
    r = _reg()
    assert r.get("k.mem", "1").id == "k.mem"
    with pytest.raises(RegistryError, match=r"no plugin k.mem@2 \(known: k.mem@1\)"):
        r.get("k.mem", "2")


def test_same_id_version_with_a_different_schema_is_refused():
    r = _reg()
    r.register(P("k.mem", schema=WINDOW))                       # idempotent
    with pytest.raises(RegistryError, match="different config_schema"):
        r.register(P("k.mem", schema={"type": "object"}))


def test_schema_hash_is_canonical():
    assert schema_hash({"a": 1, "b": [1, 2]}) == schema_hash({"b": [1, 2], "a": 1})
    assert schema_hash({"a": 1}) != schema_hash({"a": 2})


def test_validate_config_reports_json_pointer_paths():
    r = _reg()
    assert r.validate_config("k.mem", "1", {"weeks": 10}) == []
    problems = r.validate_config("k.mem", "1", {"weeks": 0, "extra": 1})
    assert any("at /weeks" in p and "minimum" in p for p in problems)
    assert any("extra" in p for p in problems)


def test_build_pipeline_aggregates_every_problem():
    r = _reg()
    with pytest.raises(ConfigError) as ei:
        r.build_pipeline({
            "context": [{"id": "k.mem", "version": "1", "config": {"weeks": 0}},
                        {"id": "k.mem", "config": {"weeks": 1}},            # no version
                        {"id": "k.nope", "version": "1"}],
            "bogus": [],
            "model": [{"id": "k.model", "version": "1"}],
            "run": [{"id": "k.run", "version": "1"}],
            "render": [{"id": "k.render", "version": "1"}],
        })
    text = "\n".join(ei.value.problems)
    assert "at /weeks" in text and "version is mandatory" in text
    assert "no plugin k.nope@1" in text and "unknown stage 'bogus'" in text
    assert len(ei.value.problems) == 4


def test_build_pipeline_success_and_single_owner_check():
    r = _reg()
    p = r.build_pipeline({
        "context": [PluginRef("k.mem", "1", {"weeks": 10})],
        "model": [{"id": "k.model", "version": "1"}],
        "run": [{"id": "k.run", "version": "1"}],
        "render": [{"id": "k.render", "version": "1"}],
    })
    assert [d["plugin"] for d in p.describe()] == ["k.mem", "k.model", "k.run", "k.render"]
    with pytest.raises(PipelineError):
        r.build_pipeline({"model": [{"id": "k.model", "version": "1"}]})


def test_describe_is_the_admin_payload():
    rows = _reg().describe()
    mem = next(x for x in rows if x["id"] == "k.mem")
    assert mem["stage"] == "context" and mem["config_schema"] == WINDOW
    assert mem["schema_hash"] == schema_hash(WINDOW) and mem["handles_money"] is False


def test_invalid_schema_is_rejected_at_registration():
    with pytest.raises(Exception):
        Registry().register(P("k.bad", schema={"type": "not-a-type"}))
