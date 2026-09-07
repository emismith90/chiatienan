"""The grader relocation changed no verdict (plan Task 4.2, review F7).

`bench.regrade` re-grades `tool_selection` from stored records with no model calls;
running it through the plugin graders over the two stored `typical` runs — one with
0 failures, one with 36 — must change nothing. The `skipped` count depends on whether
the gitignored prod corpus is present (42 prod records are "not in corpus" without it)
and, for the older file, on the `add_member` cases that carry no id map.
`ledger_state`/`prose` are covered by the pre-existing oracle tests in
`test_bench_run.py` and `test_bench_graders.py`, which now run through the shim.
"""
import json
from pathlib import Path

import pytest

from bench.regrade import regrade

RESULTS = Path(__file__).resolve().parent.parent / "bench" / "results"


def _verdicts(results):
    return [((r.get("grades") or {}).get("tool_selection") or {}).get("passed") for r in results["records"]]


@pytest.mark.parametrize("name, false_before, skipped_ok", [
    ("pi-typical-r3.json", 0, {0, 42}),
    ("pi-typical-r3-before-fixes.json", 36, {15, 57}),
])
def test_regrading_a_stored_run_through_the_plugin_graders_changes_nothing(name, false_before, skipped_ok):
    results = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    before = _verdicts(results)
    assert len(before) == 111 and before.count(False) == false_before
    changed, skipped, notes = regrade(results, "typical")
    assert changed == 0, notes
    assert skipped in skipped_ok, (skipped, notes[:3])
    assert _verdicts(results) == before and results["regraded_tool_selection"] is True
