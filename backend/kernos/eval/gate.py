"""Gate 4 — eval (design §9.4; plan Task 4.3, review F2/F4/F8/F10).

A publish whose spec names ``eval.suites`` needs, for each suite, a **completed** run
whose ``spec_sha`` equals the candidate's (``kernos.eval.spec_sha``: the stored spec
minus ``eval``). The gate never runs a model: runs are jobs (``app.evalhost``), the
gate reads their summaries. It refuses when a blocking grader graded no case, raised
on any case, or passed fewer than ``eval.gate[name]`` (default 1.0) of the graded ones.
Non-blocking graders report in the summary and never block.
"""
from __future__ import annotations

from typing import Any

from kernos.content.errors import NotFound
from kernos.content.gates import GateFailure
from kernos.eval.case import spec_sha


def latest_matching_run(store: Any, suite_id: int, sha: str) -> dict | None:
    """The newest finished run of ``suite_id`` whose content is ``sha`` — any version:
    a run of identical content is valid evidence."""
    for run in store.list_runs(suite_id=suite_id, limit=200):
        if run["spec_sha"] == sha and run["status"] != "running":
            return run
    return None


def eval_gate(store: Any, spec: Any, *, profile_id: int, version_id: int) -> list[GateFailure]:
    suites = list(spec.eval.suites or [])
    if not suites:
        return []
    business_id = store.get_profile(profile_id)["business_id"]
    sha = spec_sha(spec)
    thresholds = dict(spec.eval.gate or {})
    failures: list[GateFailure] = []
    for slug in suites:
        try:
            suite = store.get_suite(business_id, slug)
        except NotFound:
            failures.append(GateFailure("eval", f"suite {slug!r} does not exist for this business"))
            continue
        run = latest_matching_run(store, suite["id"], sha)
        if run is None:
            failures.append(GateFailure(
                "eval", f"suite {slug!r}: no completed run for this version's content — run the suite first"))
            continue
        if run["status"] != "done":
            failures.append(GateFailure("eval", f"suite {slug!r}: the latest run failed ({run.get('error') or 'no detail'})"))
            continue
        for g in (run.get("summary") or {}).get("graders") or []:
            if not g.get("blocking"):
                continue
            name = g["name"]
            graded = int(g.get("passed") or 0) + int(g.get("failed") or 0)
            if int(g.get("ungraded_grader_raised") or 0):
                failures.append(GateFailure(
                    "eval", f"suite {slug!r}: {name} raised on {g['ungraded_grader_raised']} case(s) (run #{run['id']})"))
                continue
            if graded == 0:
                failures.append(GateFailure("eval", f"suite {slug!r}: {name} graded no case (run #{run['id']})"))
                continue
            threshold = float(thresholds.get(name, 1.0))
            rate = float(g.get("rate") or 0.0)
            if rate < threshold:
                failures.append(GateFailure(
                    "eval", f"suite {slug!r}: {name} pass rate {rate:.0%} is below the gate {threshold:.0%} "
                            f"({g['failed']} of {graded} failed; run #{run['id']})"))
    return failures
