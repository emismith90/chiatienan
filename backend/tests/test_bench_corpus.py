"""The benchmark corpus loaders.

These assert the two facts that make the corpora replayable at all: golden
meals carry no message of their own (one has to be authored per case), and the
week scenario's LLM-replayable steps are a *subset* of its steps, each of which
still needs every earlier step — message-less ones included — to rebuild the
world its expectation assumes.
"""
import json

import pytest


def test_meals_corpus_pairs_every_golden_case_with_a_message():
    from bench.corpus import load
    cases = load("meals")
    assert [c.id for c in cases] == ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G12"]
    # meals.py has no messages — corpus.py must supply one per case
    assert all(c.message and c.message.startswith("@bot") for c in cases)


def test_meals_cases_have_no_prior_steps_and_expect_propose_meal():
    from bench.corpus import load
    for c in load("meals"):
        assert c.prior_steps == []
        assert c.expect["tools"] == ["propose_meal"]
        assert c.expect["args"]["propose_meal"]["total"] > 0


def test_a_golden_meal_case_without_a_message_fails_loudly():
    # Adding a case to tests/golden/meals.py must force adding a message rather
    # than silently shrinking the corpus.
    from bench import corpus
    with pytest.raises(KeyError, match="G99"):
        corpus._meal_case({"id": "G99", "payer": 1, "participants": [1], "total": 1})


def test_week_corpus_keeps_only_the_eleven_llm_replayable_steps():
    from bench.corpus import load
    ids = [c.id for c in load("week")]
    assert ids == ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9b", "s10b", "s12"]


def test_week_case_carries_every_prior_step_including_message_less_ones():
    from bench.corpus import load
    s12 = next(c for c in load("week") if c.id == "s12")
    prior = [s["id"] for s in s12.prior_steps]
    # the s11* payments are what make s12's `empty: True` true
    assert "s11a" in prior and "s11h" in prior and "s9a" in prior
    assert prior[-1] == "s11h"


def test_the_first_week_case_has_no_prior_steps():
    from bench.corpus import load
    s1 = next(c for c in load("week") if c.id == "s1")
    assert s1.prior_steps == []


def test_week_members_carry_bank_details():
    from bench.corpus import load
    # a1/a2/a4 have banks "so QR builds succeed" — without them settle_period raises
    s5 = next(c for c in load("week") if c.id == "s5")
    banked = {m["key"] for m in s5.members if m.get("bank")}
    assert banked == {"a1", "a2", "a4"}


def test_week_expectations_are_the_golden_steps_own_expectations():
    from bench.corpus import load
    from tests.golden.scenario_week import STEPS
    by_id = {s["id"]: s for s in STEPS}
    for c in load("week"):
        for key, want in by_id[c.id].get("expect", {}).items():
            assert c.expect[key] == want, f"{c.id} {key}"


def test_week_case_expects_the_tool_its_kind_implies():
    from bench.corpus import load
    got = {c.id: c.expect["tools"] for c in load("week")}
    assert got["s1"] == ["propose_meal"]
    assert got["s3"] == ["propose_payment"]
    assert got["s6"] == ["add_member"]
    assert got["s5"] == ["settle_period"]


def test_every_case_carries_its_day_and_actor():
    from bench.corpus import load
    for c in load("meals") + load("week"):
        assert c.day and len(c.day) == 10, c.id
        assert c.actor in {m["key"] for m in c.members} | {"a5"}, c.id


def test_prod_corpus_is_empty_when_the_fixture_is_absent(tmp_path, monkeypatch):
    from bench import corpus
    monkeypatch.setattr(corpus, "PROD_PATH", tmp_path / "nope.json")
    assert corpus.load("prod") == []


def test_prod_corpus_skips_cases_flagged_for_review(tmp_path, monkeypatch):
    from bench import corpus
    path = tmp_path / "prod.json"
    path.write_text(json.dumps({"members": [{"key": "A1", "display_name": "A1"}], "cases": [
        {"id": "p1", "day": "2026-07-20", "actor": "A1", "message": "@bot 300k cả nhóm",
         "expect": {"tools": ["propose_meal"]}},
        {"id": "p2", "day": "2026-07-20", "actor": "A1", "message": "@bot ?",
         "review": True, "expect": {}},
    ]}), encoding="utf-8")
    monkeypatch.setattr(corpus, "PROD_PATH", path)
    assert [c.id for c in corpus.load("prod")] == ["p1"]


def test_bills_corpus_is_empty_when_the_fixture_is_absent(tmp_path, monkeypatch):
    from bench import corpus
    monkeypatch.setattr(corpus, "BILLS_PATH", tmp_path / "nope.json")
    assert corpus.load("bills") == []


def test_all_is_the_concatenation_of_every_corpus(tmp_path, monkeypatch):
    from bench import corpus
    monkeypatch.setattr(corpus, "PROD_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(corpus, "BILLS_PATH", tmp_path / "nope.json")
    assert [c.id for c in corpus.load("all")] == \
        [c.id for c in corpus.load("meals")] + [c.id for c in corpus.load("week")]


def test_an_unknown_corpus_name_raises():
    from bench.corpus import load
    with pytest.raises(ValueError, match="nope"):
        load("nope")


def test_the_synthetic_bill_cases_load_with_their_images():
    from bench.corpus import load
    cases = load("bills")
    assert [c.id for c in cases] == ["B1", "B2", "B3"]
    for c in cases:
        assert c.had_images and len(c.images) == 1
        assert c.images[0]["mimeType"] == "image/png"
        assert c.images[0]["data"].startswith("iVBOR")      # a real PNG, base64
        assert c.expect["tools"] == ["propose_meal"]


def test_every_bill_expectation_matches_what_the_money_engine_computes():
    # The bills exist to test vision, so their arithmetic must not be the thing
    # that fails. Recompute each expectation from app.money and compare.
    from app.money import itemized_adjustments, prorate_items, split_with_guests
    from bench.corpus import load
    for case in load("bills"):
        args = case.expect["args"]["propose_meal"]
        keys = args["participants"]
        index = {k: i + 1 for i, k in enumerate(keys)}
        adjustments = {}
        if "items" in args:
            shares = prorate_items(args["total"],
                                   {index[i["member"]]: i["amount"] for i in args["items"]},
                                   discount_split="proportional")
            adjustments = itemized_adjustments(args["total"], shares)
        split = split_with_guests(args["total"], [index[k] for k in keys], 0,
                                  adjustments, payer_id=index[args["payer"]])
        want = {index[k]: v for k, v in case.expect["shares"].items()}
        assert split["shares"] == want, case.id
        assert sum(split["shares"].values()) == case.expect["tracked"], case.id
