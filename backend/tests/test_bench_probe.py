"""The tool-call validator behind `bench.probe_models`.

Offline: the HTTP POST is injected. What is being pinned here is that the checker
would actually *catch* a provider mistranslating one of our schemas — a validator
that passes everything would turn the probe into a rubber stamp on the one
capability the money path cannot do without.
"""
import json

import pytest

from bench.probe_models import validate_args

_PROPOSE = {
    "type": "object",
    "properties": {
        "payer": {"type": "integer"},
        "participants": {"type": "array", "items": {"type": "integer"}},
        "total": {"type": "integer"},
        "guests": {"type": "array", "items": {"type": "string"}},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"member": {"type": "integer"}, "amount": {"type": "integer"},
                           "label": {"type": "string"}},
            "required": ["member", "amount"]}},
        "discount_split": {"type": "string", "enum": ["proportional", "equal"]},
    },
    "required": ["participants", "total"],
}

_TARGET = {"type": "object",
           "properties": {"target": {"type": ["string", "integer"]}},
           "required": ["target"]}


def test_a_well_formed_call_validates():
    assert validate_args(_PROPOSE, {
        "participants": [1, 2], "total": 154000,
        "items": [{"member": 1, "amount": 45000, "label": "cơm tấm"}],
        "discount_split": "equal"}) == []


def test_a_missing_required_property_is_caught():
    problems = validate_args(_PROPOSE, {"total": 154000})
    assert any("participants" in p for p in problems)


def test_a_string_where_an_integer_belongs_is_caught():
    # The classic provider slip: "154000" instead of 154000. The tool would reject
    # it, so the turn would ask a clarifying question instead of logging a meal.
    problems = validate_args(_PROPOSE, {"participants": [1], "total": "154000"})
    assert any("total" in p and "integer" in p for p in problems)


def test_a_boolean_is_not_accepted_as_an_integer():
    # bool is an int in Python; a naive isinstance check would let True through.
    problems = validate_args(_PROPOSE, {"participants": [1], "total": True})
    assert any("total" in p for p in problems)


def test_a_value_outside_an_enum_is_caught():
    problems = validate_args(_PROPOSE, {"participants": [1], "total": 1,
                                        "discount_split": "prorata"})
    assert any("enum" in p for p in problems)


def test_a_nested_item_missing_its_amount_is_caught():
    problems = validate_args(_PROPOSE, {"participants": [1], "total": 1,
                                        "items": [{"member": 1, "label": "phở"}]})
    assert any("amount" in p for p in problems)


def test_a_nested_item_with_a_string_amount_is_caught():
    problems = validate_args(_PROPOSE, {"participants": [1], "total": 1,
                                        "items": [{"member": 1, "amount": "45k"}]})
    assert any("items[0].amount" in p for p in problems)


def test_a_participant_list_of_names_instead_of_ids_is_caught():
    problems = validate_args(_PROPOSE, {"participants": ["An", "Bình"], "total": 1})
    assert len([p for p in problems if "participants[" in p]) == 2


@pytest.mark.parametrize("value", ["binh", 7])
def test_the_union_target_accepts_both_of_its_declared_types(value):
    # tools.py:193 — the single union in all 14 schemas, and the one case the
    # design says a naive converter silently mistranslates.
    assert validate_args(_TARGET, {"target": value}) == []


def test_the_union_target_rejects_a_third_type():
    assert validate_args(_TARGET, {"target": True}) != []


def test_an_extra_property_the_model_volunteered_is_tolerated():
    # The tool ignores it; failing the probe for it would be stricter than reality.
    assert validate_args(_PROPOSE, {"participants": [1], "total": 1,
                                    "dish": "bún bò"}) == []


def test_required_extra_lets_the_caller_insist_the_model_filled_a_field():
    assert validate_args(_TARGET, {"target": "binh"}, required_extra=("nickname",)) != []


# --------------------------------------------------------------------------- #
# the probe loop
# --------------------------------------------------------------------------- #

def _reply(tool, args):
    return {"choices": [{"message": {"tool_calls": [
        {"function": {"name": tool, "arguments": json.dumps(args)}}]}}]}


def test_a_prose_reply_instead_of_a_tool_call_fails(monkeypatch):
    from bench import probe_models
    # This is the failure that puts arithmetic back on the model's side of the
    # wire, so it must never read as a pass.
    monkeypatch.setattr(probe_models, "_post",
                        lambda *a, **k: {"choices": [{"message": {"content": "Chắc là 154k nhé"}}]})
    monkeypatch.setattr(probe_models, "PROBES", (("settle_period", "x", ()),))
    rows = probe_models.probe("m/1", "key", {"settle_period": {"type": "object"}})
    assert rows[0]["ok"] is False and "no tool call" in rows[0]["detail"]


def test_calling_the_wrong_tool_fails(monkeypatch):
    from bench import probe_models
    monkeypatch.setattr(probe_models, "_post", lambda *a, **k: _reply("find_members", {}))
    monkeypatch.setattr(probe_models, "PROBES", (("settle_period", "x", ()),))
    rows = probe_models.probe("m/1", "key", {"settle_period": {"type": "object"}})
    assert rows[0]["ok"] is False and "find_members" in rows[0]["detail"]


def test_unparseable_arguments_fail(monkeypatch):
    from bench import probe_models
    monkeypatch.setattr(probe_models, "_post", lambda *a, **k: {"choices": [{"message": {
        "tool_calls": [{"function": {"name": "settle_period", "arguments": "{not json"}}]}}]})
    monkeypatch.setattr(probe_models, "PROBES", (("settle_period", "x", ()),))
    rows = probe_models.probe("m/1", "key", {"settle_period": {"type": "object"}})
    assert rows[0]["ok"] is False and "not JSON" in rows[0]["detail"]


def test_a_rate_limit_is_retried_rather_than_recorded_as_a_capability_failure():
    from urllib.error import HTTPError
    from bench import probe_models
    calls = {"n": 0}

    def flaky(url, data=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise HTTPError(url, 429, "Too Many Requests", {}, None)
        class _R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(_reply("settle_period", {})).encode()
        return _R()

    import bench.probe_models as pm
    real_urlopen = pm.urlopen
    try:
        pm.urlopen = flaky
        body = pm._post({"model": "m"}, "key", sleep=lambda _s: None)
    finally:
        pm.urlopen = real_urlopen
    assert calls["n"] == 3
    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "settle_period"
