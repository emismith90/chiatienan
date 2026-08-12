"""The report, and the comparison that decides whether Pi ships.

Because `--repeat` produces N records per case, the unit of comparison is a
**pass rate**, not a verdict: both engines are nondeterministic and they are
different models, so a single flip is indistinguishable from sampling noise.

Two rows in the compare table exist purely to stop the report lying by omission.
A case present in one run and absent from the other, and a case both runs failed
outright, would each otherwise render as a quiet "0.00" delta — reading as "no
change" when nothing was actually compared.
"""
import json

import pytest

GRADERS = ("tool_selection", "ledger_state", "prose_quality")


def _rec(case_id, rep=0, source="week", elapsed_s=1.0, stats=None, error=None, **grades):
    return {"case_id": case_id, "rep": rep, "source": source, "elapsed_s": elapsed_s,
            "stats": stats, "error": error, "tools": [], "final_text": "",
            "grades": {g: {"passed": grades.get(g, True), "reason": "-"} for g in GRADERS}}


def _results(records, engine="cursor", **kw):
    return {"version": 1, "engine": engine, "corpus": "all", "repeat": 3,
            "judge_model": kw.pop("judge_model", "test/judge"), "records": records, **kw}


def test_a_case_rate_counts_only_graded_repetitions():
    from bench.report import case_rates
    recs = [_rec("s1", 0, tool_selection=True), _rec("s1", 1, tool_selection=False),
            _rec("s1", 2, tool_selection=None)]
    rates = case_rates(_results(recs))
    # 1 of the 2 that were graded — the ungraded repetition is not a failure
    assert rates["s1"]["tool_selection"] == (1, 2)


def test_a_fully_ungraded_case_is_na_not_zero():
    from bench.report import case_rates, format_rate
    rates = case_rates(_results([_rec("s6", prose_quality=None)]))
    assert rates["s6"]["prose_quality"] == (0, 0)
    assert format_rate((0, 0)) == "n/a"


def test_the_markdown_report_names_the_engine_the_judge_and_every_case():
    from bench.report import render
    out = render(_results([_rec("s1"), _rec("s5", ledger_state=False)]))
    assert "cursor" in out and "test/judge" in out
    assert "s1" in out and "s5" in out
    for grader in GRADERS:
        assert grader in out


def test_the_report_says_so_when_no_judge_was_configured():
    from bench.report import render
    out = render(_results([_rec("s1", prose_quality=None)], judge_model=None))
    # A run with no judge must not read as a run whose prose passed.
    assert "no judge" in out.lower()


def test_the_report_surfaces_errors_rather_than_burying_them():
    from bench.report import render
    out = render(_results([_rec("s1", error="bridge died", tool_selection=False)]))
    assert "bridge died" in out


def test_the_report_carries_latency_and_says_unknown_for_missing_cost():
    from bench.report import render
    out = render(_results([_rec("s1", elapsed_s=4.0), _rec("s2", elapsed_s=8.0)]))
    assert "p95" in out
    assert "unknown" in out.lower()          # Cursor exposes no cost


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #

def test_compare_surfaces_a_case_whose_pass_rate_dropped():
    from bench.report import render_compare
    base = _results([_rec("s5", i, ledger_state=True) for i in range(3)])
    new = _results([_rec("s5", i, ledger_state=(i == 0)) for i in range(3)], engine="pi")
    out = render_compare(base, new)
    assert "s5" in out and "-0.67" in out


def test_compare_surfaces_an_improvement_too():
    from bench.report import render_compare
    base = _results([_rec("s5", i, ledger_state=False) for i in range(3)])
    new = _results([_rec("s5", i, ledger_state=True) for i in range(3)], engine="pi")
    assert "+1.00" in render_compare(base, new)


def test_a_case_missing_from_one_run_gets_an_explicit_missing_row():
    from bench.report import render_compare
    # A silently dropped case reads as "no change" — the exact failure this
    # harness exists to prevent.
    base = _results([_rec("s5"), _rec("s12")])
    new = _results([_rec("s5")], engine="pi")
    out = render_compare(base, new)
    assert "MISSING" in out and "s12" in out


def test_a_case_both_runs_failed_gets_an_explicit_both_failing_row():
    from bench.report import render_compare
    # A vacuous grader fails identically on both engines; a 0.00 delta would
    # dress that up as equivalence.
    base = _results([_rec("s5", i, ledger_state=False) for i in range(3)])
    new = _results([_rec("s5", i, ledger_state=False) for i in range(3)], engine="pi")
    out = render_compare(base, new)
    assert "BOTH-FAILING" in out


def test_an_ungraded_grader_is_na_in_compare_and_not_folded_into_a_rate():
    from bench.report import render_compare, ship_blockers
    base = _results([_rec("s6", prose_quality=None)])
    new = _results([_rec("s6", prose_quality=None)], engine="pi")
    assert "n/a" in render_compare(base, new)
    # ungraded is not failing, so it raises no flag and blocks nothing
    assert ship_blockers(base, new) == []


def test_compare_reports_the_ship_criterion_verdict():
    from bench.report import render_compare, ship_blockers
    # "no case whose tool_selection or ledger_state pass rate dropped by more
    # than 1/3"
    base = _results([_rec("s5", i, ledger_state=True) for i in range(3)])
    new = _results([_rec("s5", i, ledger_state=(i == 0)) for i in range(3)], engine="pi")
    blockers = ship_blockers(base, new)
    assert any(b["case_id"] == "s5" and b["grader"] == "ledger_state" for b in blockers)
    assert "s5" in render_compare(base, new)


def test_a_drop_of_exactly_one_third_is_not_a_blocker():
    from bench.report import ship_blockers
    base = _results([_rec("s5", i, ledger_state=True) for i in range(3)])
    new = _results([_rec("s5", i, ledger_state=(i < 2)) for i in range(3)], engine="pi")
    assert ship_blockers(base, new) == []


def test_a_prose_drop_is_not_a_ship_blocker_but_is_still_shown():
    from bench.report import render_compare, ship_blockers
    base = _results([_rec("s6", i, prose_quality=True) for i in range(3)])
    new = _results([_rec("s6", i, prose_quality=False) for i in range(3)], engine="pi")
    # The criterion names tool_selection and ledger_state; prose has to be
    # "understood and written down", not auto-blocking.
    assert ship_blockers(base, new) == []
    assert "s6" in render_compare(base, new)


def test_a_both_failing_case_is_a_blocker_because_it_certified_nothing():
    from bench.report import ship_blockers
    base = _results([_rec("s5", i, ledger_state=False) for i in range(3)])
    new = _results([_rec("s5", i, ledger_state=False) for i in range(3)], engine="pi")
    blockers = ship_blockers(base, new)
    assert any("BOTH-FAILING" in b["kind"] for b in blockers)


def test_a_missing_case_is_a_blocker():
    from bench.report import ship_blockers
    base = _results([_rec("s5"), _rec("s12")])
    new = _results([_rec("s5")], engine="pi")
    assert any(b["case_id"] == "s12" for b in ship_blockers(base, new))


def test_compare_refuses_to_pretend_two_differently_judged_runs_are_comparable():
    from bench.report import render_compare
    base = _results([_rec("s1")], judge_model=None)
    new = _results([_rec("s1")], engine="pi", judge_model="test/judge")
    out = render_compare(base, new)
    assert "judge" in out.lower() and ("differ" in out.lower() or "not comparable" in out.lower())


def test_the_cli_renders_a_results_file(tmp_path, capsys):
    from bench.report import main
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_results([_rec("s1")])), encoding="utf-8")
    assert main([str(path)]) == 0
    assert "s1" in capsys.readouterr().out


def test_the_cli_compares_two_results_files(tmp_path, capsys):
    from bench.report import main
    base, new = tmp_path / "b.json", tmp_path / "n.json"
    base.write_text(json.dumps(_results([_rec("s1")])), encoding="utf-8")
    new.write_text(json.dumps(_results([_rec("s1")], engine="pi")), encoding="utf-8")
    assert main(["--compare", str(base), str(new)]) == 0
    out = capsys.readouterr().out
    assert "cursor" in out and "pi" in out
