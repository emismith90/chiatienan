import pytest
from pydantic import ValidationError

from kernos.content import Models, PipelineEntry, ProfileSpec, Rule, Runtime, Skill, StaticResolver


def _spec(**kw):
    base = dict(models=Models(text="m", vision="v", thinking="low"),
                runtime=Runtime(cwd="/c", agent_dir="/a"),
                skills=[Skill(name="a", body="A"), Skill(name="d", body="D", delivery="discoverable")],
                rules=[Rule(slug="money-safety", content="R", tags=["money"])],
                builtin_tools=["read"])
    base.update(kw)
    return ProfileSpec(**base)


def test_to_engine_spec_uses_todays_wire_shapes_and_only_inline_skills():
    es = _spec().to_engine_spec(system="S")
    assert es.model == "m" and es.vision_model == "v" and es.thinking == "low"
    assert es.skills == [{"name": "a", "description": "", "body": "A"}]
    assert es.context_files == [{"path": "money-safety", "content": "R"}]
    assert es.cwd == "/c" and es.agent_dir == "/a" and es.system == "S"
    assert es.builtin_tools == ["read"] and es.max_tools == 40 and es.max_seconds == 120
    assert es.settings == {} and es.extensions == []


def test_unknown_fields_are_rejected_everywhere():
    with pytest.raises(ValidationError):
        ProfileSpec(models=Models(text="m"), bogus=1)
    with pytest.raises(ValidationError):
        Models(text="m", temperature=0.2)


def test_pipeline_dict_and_static_resolver():
    spec = _spec(pipeline={"context": [PipelineEntry(id="k.mem", version="1", config={"weeks": 10})]})
    assert spec.pipeline_dict() == {"context": [{"id": "k.mem", "version": "1", "config": {"weeks": 10}}]}
    assert StaticResolver(spec).resolve("any") is spec


def test_spec_round_trips_through_json():
    spec = _spec(settings={"compaction": {"enabled": False}}, meta={"handles_money": True})
    again = ProfileSpec.model_validate_json(spec.model_dump_json())
    assert again == spec and again.meta["handles_money"] is True
