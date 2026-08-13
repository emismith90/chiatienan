"""Re-grading a stored run, for when the *judgement* changed and not the engine.

`ledger_state` is deliberately untouchable here: it reads a database that no longer
exists by the time a report is rendered.
"""
import json

from bench.corpus import Case
from bench.regrade import member_ids, regrade


def _results(**overrides):
    record = {
        "version": 1, "case_id": "p10", "rep": 0, "source": "prod",
        "day": "2026-07-22", "message": "@bot room balance now", "had_images": False,
        "sender_member_id": 1, "member_ids": {"A1": 1, "A2": 2},
        "tools": [{"name": "get_period_summary", "args": {}, "result": {"ok": True}}],
        "final_text": "Không ai nợ ai.", "error": None, "elapsed_s": 1.0,
        "grades": {"tool_selection": {"passed": False, "reason": "expected settle_period"},
                   "ledger_state": {"passed": None, "reason": "no ledger expectation"},
                   "prose_quality": {"passed": True, "reason": "fine"}},
    }
    record.update(overrides)
    return {"version": 1, "engine": "pi", "corpus": "prod", "records": [record]}


def _p10_prod_fixture(tmp_path):
    # The real prod corpus is gitignored and absent on a fresh checkout, so
    # `corpus.load("prod")` would never find "p10" there. A synthetic fixture
    # with p10's real expectation (settle_period) lets `prod_judgements`'
    # committed widening for p10 (get_period_summary) do its job.
    path = tmp_path / "prod.json"
    path.write_text(json.dumps({
        "members": [{"key": "A1", "display_name": "A1"}, {"key": "A2", "display_name": "A2"}],
        "cases": [
            {"id": "p10", "day": "2026-07-22", "actor": "A1",
             "message": "@bot room balance now",
             "expect": {"tools": ["settle_period"]}},
        ],
    }), encoding="utf-8")
    return path


def test_a_judged_alternative_flips_a_stored_verdict_without_a_rerun(tmp_path, monkeypatch):
    from bench import corpus
    monkeypatch.setattr(corpus, "PROD_PATH", _p10_prod_fixture(tmp_path))
    results = _results()
    changed, skipped, notes = regrade(results, "prod")
    grades = results["records"][0]["grades"]
    # p10's judgement (get_period_summary answers "room balance now") is committed,
    # so the stored False becomes True — no model call involved.
    assert grades["tool_selection"]["passed"] is True
    assert "judged alternative" in grades["tool_selection"]["reason"]
    assert changed == 1 and skipped == 0
    assert results["regraded_tool_selection"] is True


def test_ledger_state_and_prose_verdicts_are_carried_through_untouched():
    results = _results()
    regrade(results, "prod")
    grades = results["records"][0]["grades"]
    assert grades["ledger_state"] == {"passed": None, "reason": "no ledger expectation"}
    assert grades["prose_quality"] == {"passed": True, "reason": "fine"}


def test_a_record_for_a_case_outside_the_corpus_is_skipped_not_guessed():
    results = _results(case_id="nope")
    changed, skipped, notes = regrade(results, "prod")
    assert (changed, skipped) == (0, 1)
    assert "not in corpus" in notes[0]


def test_ids_are_reconstructed_from_the_seed_order_when_absent():
    case = Case(id="x", source="prod", day="2026-07-22", actor="A1",
                members=[{"key": "A1"}, {"key": "A2"}, {"key": "A3"}])
    assert member_ids(case, {}) == {"A1": 1, "A2": 2, "A3": 3}


def test_a_case_that_adds_a_member_mid_scenario_gets_no_guessed_map():
    case = Case(id="s7", source="week", day="2026-07-23", actor="a1",
                members=[{"key": "a1"}],
                prior_steps=[{"kind": "add_member", "new_member": "a5"}])
    assert member_ids(case, {}) is None


def test_a_stored_map_wins_over_reconstruction():
    case = Case(id="x", source="prod", day="2026-07-22", actor="A1",
                members=[{"key": "A1"}, {"key": "A2"}])
    assert member_ids(case, {"member_ids": {"A1": 7, "A2": 9}}) == {"A1": 7, "A2": 9}
