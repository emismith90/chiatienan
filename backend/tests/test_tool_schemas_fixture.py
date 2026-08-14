"""The committed schema fixture must match the live tools.

Python builds the 14 schemas; the Node sidecar converts them to TypeBox. If the
fixture drifts from `build_tools`, the model is told about a shape the tool does
not accept, and the symptom is a clarifying question where a logged meal should
be — not an error anyone would trace back to here.
"""
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "agent_sidecar/test/fixtures/tool-schemas.json"


def test_committed_schema_fixture_matches_the_live_tools():
    from bench.dump_schemas import live_schemas
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture == live_schemas(), "regenerate with: python -m bench.dump_schemas"


def test_the_fixture_covers_every_tool():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(fixture) == 16
    assert all(s["type"] == "object" for s in fixture.values())


def test_the_shared_period_enum_is_resolved_not_left_as_a_reference():
    # `_PERIOD_SCHEMA["properties"]["keyword"]` is reused by four tools; a fixture
    # built from the literals rather than the imported module would lose it.
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with_keyword = {name for name, s in fixture.items()
                    if (s.get("properties") or {}).get("keyword", {}).get("enum")}
    assert {"resolve_period", "settle_period", "member_statement",
            "get_period_summary"} <= with_keyword


def test_the_union_target_survives_serialization():
    # tools.py:193 and :216 — the only union in all 14 schemas, and the one case a
    # naive converter silently mistranslates.
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for tool in ("update_member", "delete_member"):
        assert fixture[tool]["properties"]["target"]["type"] == ["string", "integer"]


def test_only_the_six_supported_keywords_appear_anywhere():
    # design §6: the converter supports exactly these. Anything else in a schema
    # would be silently dropped or would have to throw.
    allowed = {"type", "properties", "required", "items", "description", "enum"}
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def walk(node, path):
        if not isinstance(node, dict):
            return
        for key in node:
            assert key in allowed, f"{path}: unsupported keyword {key!r}"
        for key, child in (node.get("properties") or {}).items():
            walk(child, f"{path}.{key}")
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]")

    for name, schema in fixture.items():
        walk(schema, name)
