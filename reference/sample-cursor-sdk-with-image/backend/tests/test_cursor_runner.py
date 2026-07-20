from app.cursor_runner import _parse_model_specifier, _build_alias_index, model_list_item_to_selection


def test_parse_model_specifier_strips_fast_suffix():
    base, overrides = _parse_model_specifier("composer-2.5-fast")
    assert base == "composer-2.5"
    assert overrides == [__import__("cursor_sdk").ModelParameterValue(id="fast", value="true")]


def test_parse_model_specifier_no_suffix():
    base, overrides = _parse_model_specifier("gemini-2.5-pro")
    assert base == "gemini-2.5-pro"
    assert overrides is None


def test_build_alias_index_first_claimant_wins():
    items = [
        {"id": "gpt-5.5", "aliases": ["gpt"]},
        {"id": "gpt-5.4", "aliases": ["gpt"]},
        {"id": "claude-opus-4-8", "aliases": ["opus-4-8", "Opus"]},
    ]
    idx = _build_alias_index(items)
    assert idx["gpt"] == "gpt-5.5"
    assert idx["opus-4-8"] == "claude-opus-4-8"
    assert idx["opus"] == "claude-opus-4-8"
