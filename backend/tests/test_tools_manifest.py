"""The tool manifest, and the proof the 16 tools were left alone."""
from pathlib import Path


def test_tool_manifest_covers_every_tool_with_a_schema(db):
    from app.tools import ToolContext, build_tools, tool_manifest
    names = set(build_tools(ToolContext(db=db, room_id=1)))
    manifest = {t["name"] for t in tool_manifest()}
    assert manifest == names
    assert len(manifest) == 16
    assert all(t["description"] and t["schema"]["type"] == "object" for t in tool_manifest())


def test_tools_module_no_longer_imports_the_vendor_sdk():
    assert "cursor_sdk" not in Path("app/tools.py").read_text(encoding="utf-8")


def test_the_manifest_matches_the_committed_sidecar_fixture():
    # Both runtimes must be told about the same shapes; the sidecar converts this
    # fixture to TypeBox.
    import json
    from app.tools import tool_manifest
    fixture = json.loads(
        Path("agent_sidecar/test/fixtures/tool-schemas.json").read_text(encoding="utf-8"))
    assert {t["name"]: t["schema"] for t in tool_manifest()} == fixture
