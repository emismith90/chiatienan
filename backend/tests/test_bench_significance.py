"""Is a blocker distinguishable from sampling noise? (`bench.report.p_value_worse`)

Phase 11's benchmark tripped the ship criterion on `B3` — 3/3 → 1/3 on both money
graders — and the investigation that followed established that `B3` genuinely passes
about 60% of the time. At that rate `--repeat 3` yields 3/3 about 22% of the time and
1/3 about 29%, so the "drop" was two ordinary draws from one distribution.

The criterion itself is not changed by any of this: a pre-existing test
(`test_bench_report.py::test_compare_reports_the_ship_criterion_verdict`) pins 3/3 → 1/3
as a blocker, and it stays one. What is added is the *verdict on the evidence*, so nobody
else spends an afternoon proving a coin flip was a coin flip.
"""
from bench.report import NOISE_ALPHA, p_value_worse, ship_blockers
from tests.test_bench_report import _rec, _results


def _run(case, passes, n, *, engine="cursor"):
    """`passes` of `n` attempts pass `ledger_state`."""
    return _results([_rec(case, i, ledger_state=(i < passes)) for i in range(n)], engine=engine)


# ------------------------------------------------------------------ the maths

def test_the_p_value_matches_the_hypergeometric_by_hand():
    # 3 of 3 then 1 of 3: margins are 6 trials, 4 passes, 3 per group, so
    # P(candidate <= 1) = C(4,1)C(2,2)/C(6,3) = 4/20
    assert p_value_worse(3, 3, 1, 3) == 0.2
    # 3 of 3 then 0 of 3 — the only table that extreme: C(3,0)C(3,3)/C(6,3) = 1/20
    assert p_value_worse(3, 3, 0, 3) == 0.05
    # a real drop with real samples behind it
    assert p_value_worse(9, 15, 1, 15) < 0.01
    # no drop at all is the least surprising thing there is
    assert p_value_worse(3, 3, 3, 3) == 1.0
    assert p_value_worse(3, 3, 2, 3) == 0.5


def test_the_p_value_is_a_probability_for_every_shape_including_the_degenerate_ones():
    for base_n in range(0, 6):
        for new_n in range(0, 6):
            for bp in range(0, base_n + 1):
                for np_ in range(0, new_n + 1):
                    p = p_value_worse(bp, base_n, np_, new_n)
                    assert 0.0 <= p <= 1.0, (bp, base_n, np_, new_n, p)
    assert p_value_worse(0, 0, 0, 0) == 1.0            # nothing sampled, nothing to say


def test_more_samples_make_the_same_ratio_more_significant():
    """The point of the fix: the ratio is not the evidence, the sample size is."""
    ps = [p_value_worse(n, n, n // 3, n) for n in (3, 6, 15, 30)]
    assert ps == sorted(ps, reverse=True)              # monotonically more convincing
    assert ps[0] > NOISE_ALPHA and ps[-1] < 0.001


# --------------------------------------------------------- what a blocker says

def test_the_b3_pattern_still_blocks_but_is_labelled_within_noise():
    base, new = _run("B3", 3, 3), _run("B3", 1, 3, engine="pi")
    rows = [b for b in ship_blockers(base, new) if b["case_id"] == "B3"]
    assert rows, "3/3 -> 1/3 must remain a blocker — a pre-existing test pins it"
    row = rows[0]
    assert row["kind"] == "DROP" and row["significant"] is False
    assert row["p_value"] == 0.2
    assert "WITHIN NOISE" in row["detail"] and "3+3 samples" in row["detail"]


def test_the_same_drop_measured_properly_is_reported_as_real():
    base, new = _run("B3", 15, 15), _run("B3", 5, 15, engine="pi")
    row = next(b for b in ship_blockers(base, new) if b["case_id"] == "B3")
    assert row["significant"] is True
    assert "unlikely to be sampling noise" in row["detail"]
    assert "WITHIN NOISE" not in row["detail"]


def test_a_total_collapse_is_significant_even_at_three_samples():
    base, new = _run("s5", 3, 3), _run("s5", 0, 3, engine="pi")
    row = next(b for b in ship_blockers(base, new) if b["case_id"] == "s5")
    assert row["p_value"] == 0.05 and row["significant"] is True


def test_missing_and_both_failing_rows_are_untouched_by_any_of_this():
    """They are not pass-rate comparisons, so a p-value would be meaningless."""
    base, new = _results([_rec("s5"), _rec("s12")]), _results([_rec("s5")], engine="pi")
    missing = next(b for b in ship_blockers(base, new) if b["case_id"] == "s12")
    assert missing["kind"] == "MISSING" and "p_value" not in missing

    both = _run("s5", 0, 3), _run("s5", 0, 3, engine="pi")
    row = next(b for b in ship_blockers(*both) if b["kind"] == "BOTH-FAILING")
    assert "p_value" not in row


# ------------------------------------------------------------- the rendered page

def test_compare_tells_the_operator_what_to_run_when_a_blocker_is_noise():
    from bench.report import render_compare
    out = render_compare(_run("B3", 3, 3), _run("B3", 1, 3, engine="pi"))
    assert "cannot be distinguished from sampling noise" in out
    assert "--repeat 15 --case B3" in out
    assert "python -m bench.report /tmp/recheck.json" in out
    # and it says which kinds of case that reasoning applies to
    assert "bills" in out and "close to deterministic" in out


def test_compare_says_nothing_about_noise_when_the_drop_is_real():
    from bench.report import render_compare
    out = render_compare(_run("B3", 15, 15), _run("B3", 5, 15, engine="pi"))
    assert "1 blocker(s):" in out
    assert "cannot be distinguished from sampling noise" not in out
    assert "--repeat 15" not in out
