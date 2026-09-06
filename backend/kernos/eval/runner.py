"""The eval runner (design §5.5; plan Task 4.2, review F2/F9/F10).

Kernel orchestration over host services: for every case and repetition the host's
``world_factory(case)`` rebuilds the case's world, ``run_turn(case, world, spec)``
runs one turn against the **candidate** spec, the graders judge while the world is
alive, and a summary per grader plus cost/latency becomes the run. Three properties,
each because the alternative quietly lies:

* one case failing never kills the run — a ``run_turn``/world exception becomes
  ``record["error"]``, which the money graders grade as a failure, never a skip;
* a grader that raises is ``passed: None`` **and** counted as
  ``ungraded_grader_raised``, distinct from "no expectation" — gate 4 refuses a run
  with any of those on a blocking grader;
* ``review: true`` cases are never graded: recorded as skipped.
"""
from __future__ import annotations

import time
import traceback
from typing import Any, Callable, Iterable

from kernos.eval.case import RECORD_VERSION
from kernos.eval.graders import Grader, Verdict, summarize_cost_latency


def invocation_dicts(result) -> list[dict]:
    """`TurnResult.tools` → plain dicts the graders and JSON both accept."""
    out = []
    for inv in getattr(result, "tools", None) or []:
        out.append({"name": getattr(inv, "name", None),
                    "args": getattr(inv, "args", None),
                    "result": getattr(inv, "result", None)})
    return out


def grade_record(graders: Iterable[tuple[str, Grader]], case, record: dict, world) -> dict:
    """Every grader, each isolated from the others' failures."""
    graded = {}
    for name, grader in graders:
        try:
            verdict: Verdict = grader.grade(case, record, world)
            graded[name] = {"passed": verdict.passed, "reason": verdict.reason}
        except Exception as exc:  # a broken grader must not lose the whole record
            graded[name] = {"passed": None, "reason": f"grader raised: {exc!r}", "raised": True}
    return graded


def summarize(graders: Iterable[tuple[str, Grader]], records: list[dict]) -> dict:
    per_grader = []
    graded_records = [r for r in records if not r.get("skipped")]
    for name, grader in graders:
        verdicts = [(r.get("grades") or {}).get(name) or {} for r in graded_records]
        passed = sum(1 for v in verdicts if v.get("passed") is True)
        failed = sum(1 for v in verdicts if v.get("passed") is False)
        raised = sum(1 for v in verdicts if v.get("passed") is None and v.get("raised"))
        none = sum(1 for v in verdicts if v.get("passed") is None and not v.get("raised"))
        per_grader.append({
            "name": name, "blocking": bool(getattr(grader, "blocking", False)),
            "passed": passed, "failed": failed,
            "ungraded_no_expectation": none, "ungraded_grader_raised": raised,
            "rate": (passed / (passed + failed)) if (passed + failed) else None,
        })
    return {
        "graders": per_grader,
        "records": len(records),
        "skipped_review": sum(1 for r in records if r.get("skipped") == "review"),
        "cost_latency": summarize_cost_latency(graded_records),
    }


class Runner:
    def __init__(self, graders: list[tuple[str, Grader]], *, world_factory: Callable[[Any], Any],
                 run_turn: Callable[..., Any], repeat: int = 1, judge_model: str | None = None) -> None:
        self.graders = list(graders)
        self._world_factory, self._run_turn = world_factory, run_turn
        self.repeat, self.judge_model = max(1, int(repeat)), judge_model

    async def run_one(self, case, rep: int, spec) -> dict:
        record = {"version": RECORD_VERSION, "case_id": case.id, "rep": rep, "source": case.source,
                  "day": case.day, "message": case.message, "had_images": bool(case.had_images),
                  "room_id": None, "sender_member_id": None, "member_ids": {}, "tools": [],
                  "final_text": "", "error": None, "elapsed_s": 0.0, "stats": None, "grades": {}}
        if getattr(case, "review", False):
            record["skipped"] = "review"
            return record
        started = time.monotonic()
        world = None
        try:
            world = self._world_factory(case)
            record["room_id"] = getattr(world, "space_id", None)
            record["member_ids"] = dict(getattr(world, "ids", {}) or {})
            record["sender_member_id"] = record["member_ids"].get(case.actor)
            result = await self._run_turn(case, world, spec)
            record["elapsed_s"] = time.monotonic() - started
            record["tools"] = invocation_dicts(result)
            record["final_text"] = getattr(result, "final_text", "") or ""
            record["error"] = getattr(result, "error", None)
            record["stats"] = getattr(result, "stats", None)
            record["capped"] = bool(getattr(result, "capped", False))
        except Exception as exc:  # one bad case is a data point, not the end of the run
            record["elapsed_s"] = record["elapsed_s"] or time.monotonic() - started
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["traceback"] = traceback.format_exc(limit=6)
        try:
            record["grades"] = grade_record(self.graders, case, record, world)
        finally:
            close = getattr(world, "close", None)
            if close:
                close()
        return record

    async def run(self, cases: Iterable[Any], spec, *, on_record: Callable[[dict], None] | None = None) -> dict:
        records: list[dict] = []
        for case in cases:
            for rep in range(self.repeat):
                record = await self.run_one(case, rep, spec)
                records.append(record)
                if on_record:
                    on_record(record)
        summary = summarize(self.graders, records)
        summary["judge_model"] = self.judge_model
        summary["repeat"] = self.repeat
        return {"records": records, "summary": summary, "judge_model": self.judge_model}
