"""Capabilities and the self-change scope (design §8.3; plan Task 8.1, review F7/F13)."""
import pytest

from kernos.content import (
    BLACKLIST_FIELDS, NEVER_IN_SCOPE, SCOPE_VOCABULARY, Invalid, Models, ProfileSpec, Rule, Runtime,
    ValidationRuleRef, agent_capabilities, changed_paths, normalise_capabilities, outside_scope,
)


def _spec(**kw):
    base = dict(models=Models(text="m"), runtime=Runtime(cwd="/c", agent_dir="/a"),
                rules=[Rule(slug="money-safety", content="R", tags=["money"]), Rule(slug="tone", content="T")],
                validation=[ValidationRuleRef(id="w", scope="reply", plugin="p", on_fail="warn"),
                            ValidationRuleRef(id="b", scope="reply", plugin="p", on_fail="block")])
    base.update(kw)
    return ProfileSpec(**base)


def test_capabilities_vocabulary_and_defaults():
    assert normalise_capabilities(None) == {} and normalise_capabilities({}) == {}
    out = normalise_capabilities({"cms": ["eval", "read", "read"], "self_change_scope": ["rules", "prompt.append"],
                                  "max_eval_runs_per_day": 0})
    assert out == {"cms": ["read", "eval"], "self_change_scope": ["prompt.append", "rules"], "max_eval_runs_per_day": 0}
    for bad, needle in [({"cms": ["admin"]}, "capabilities.cms"), ({"cms": "read"}, "capabilities.cms"),
                        ({"self_change_scope": ["models"]}, "self_change_scope"),
                        ({"self_change_scope": ["persona"]}, "self_change_scope"),
                        ({"max_eval_runs_per_day": 11}, "0–10"), ({"max_eval_runs_per_day": True}, "0–10"),
                        ({"max_self_iterations": 3}, "unknown capabilities keys"), ([], "must be an object")]:
        with pytest.raises(Invalid, match=needle):
            normalise_capabilities(bad)
    assert agent_capabilities(None) == {"cms": set(), "scope": [], "max_eval_runs_per_day": 2}
    assert agent_capabilities({"capabilities": {"cms": ["read", "draft"], "max_eval_runs_per_day": 5}}) == {
        "cms": {"read", "draft"}, "scope": [], "max_eval_runs_per_day": 5}
    assert not set(SCOPE_VOCABULARY) & set(BLACKLIST_FIELDS) and not set(SCOPE_VOCABULARY) & NEVER_IN_SCOPE


def test_changed_paths_names_every_spec_field_and_refines_the_split_ones():
    base = _spec().stored()
    assert changed_paths(base, base) == []
    assert changed_paths(_spec(), base) == []                                  # a resolved spec compares as stored
    for field in ProfileSpec.model_fields:
        if field == "runtime":
            continue
        mutated = dict(base)
        if field == "prompt":
            mutated["prompt"] = {**base["prompt"], "append": ["more"]}
            assert changed_paths(base, mutated) == ["prompt.append"]
            mutated["prompt"] = {**base["prompt"], "body": "new"}
            assert changed_paths(base, mutated) == ["prompt.body"]
        elif field == "rules":
            mutated["rules"] = [{**base["rules"][0], "content": "R2"}, base["rules"][1]]
            assert changed_paths(base, mutated) == ["rules[tag=money]"]
            mutated["rules"] = [base["rules"][0], {**base["rules"][1], "content": "T2"}]
            assert changed_paths(base, mutated) == ["rules"]
            mutated["rules"] = [{**base["rules"][0], "tags": []}, base["rules"][1]]        # dropping the tag is both
            assert changed_paths(base, mutated) == ["rules", "rules[tag=money]"]
        elif field == "validation":
            mutated["validation"] = [{**base["validation"][0], "config": {"x": 1}}, base["validation"][1]]
            assert changed_paths(base, mutated) == ["validation.warn"]
            mutated["validation"] = [{**base["validation"][0], "on_fail": "block"}, base["validation"][1]]
            assert changed_paths(base, mutated) == ["validation.warn", "validation[on_fail=block|scope=tool_*]"]
        else:
            mutated[field] = {"__changed__": True}
            assert changed_paths(base, mutated) == [field], field


def test_outside_scope_never_allows_the_fenced_paths():
    base = _spec().stored()
    ok = dict(base, prompt={**base["prompt"], "append": ["be brief"]})
    assert outside_scope(base, ok, ["prompt.append"]) == []
    assert outside_scope(base, ok, ["skills"]) == ["prompt.append"]
    for path, mutate in [
        ("rules[tag=money]", lambda d: d.update(rules=[{**d["rules"][0], "content": "x"}, d["rules"][1]])),
        ("validation[on_fail=block|scope=tool_*]", lambda d: d.update(validation=[d["validation"][0]])),
        ("models", lambda d: d.update(models={"text": "other"})),
        ("meta", lambda d: d.update(meta={"handles_money": False})),
        ("retry", lambda d: d.update(retry={"enabled": False})),
        ("caps", lambda d: d.update(caps={"max_tools": 1})),
        ("persona", lambda d: d.update(persona={"handle": "x"})),
    ]:
        mutated = dict(base)
        mutate(mutated)
        listed = list(SCOPE_VOCABULARY) + [path]                      # even when a scope names it
        assert path in outside_scope(base, mutated, listed), path
