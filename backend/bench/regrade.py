"""Re-grade `tool_selection` on an existing results file, without re-running it.

    python -m bench.regrade bench/results/pi-prod-r1.json --corpus prod

**Why this exists.** A judgement about what counts as a right answer
(`corpus/prod_judgements.py`) or a fix to the grader itself changes verdicts, not
model behavior — and re-running 107 real turns to learn that costs 45 minutes of
provider time and produces *different* turns, which muddles the very comparison
being made. The record already holds everything `grade_tool_selection` reads: the
tool calls, their arguments, their results, the sender, and (since this module was
added) the key→id map.

**`ledger_state` is never re-graded.** It reads the database, and the per-case
database is deleted when the case ends — by design (`bench.run`'s docstring). Its
stored verdicts are carried through untouched, and a run whose `ledger_state` needs
revisiting has to be re-run. Same for `prose_quality`: its judge is not available
here.

The output is stamped `regraded_tool_selection: true` so nobody mistakes it for a
fresh measurement of the engine.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bench import corpus, graders


def member_ids(case, record: dict) -> dict[str, int] | None:
    """The key→id map for this case, from the record or reconstructed.

    Reconstruction is only sound when the room's membership never changed during
    the case: `bench.world._seed_room` inserts `case.members` in order into a fresh
    database, so ids are `1..n` in that order. A case with an `add_member` step
    breaks that, and those return None rather than a plausible-looking guess.
    """
    stored = record.get("member_ids")
    if stored:
        return {k: int(v) for k, v in stored.items()}
    if any(step.get("kind") == "add_member" for step in case.prior_steps or []):
        return None
    keys = [spec.get("key") for spec in case.members or []]
    if not keys or any(k is None for k in keys):
        return None
    return {key: index for index, key in enumerate(keys, start=1)}


def regrade(results: dict, corpus_name: str) -> tuple[int, int, list[str]]:
    """Recompute `tool_selection` in place. Returns `(changed, skipped, notes)`."""
    cases = {case.id: case for case in corpus.load(corpus_name)}
    changed, skipped, notes = 0, 0, []

    for record in results.get("records") or []:
        case = cases.get(record.get("case_id"))
        if case is None:
            skipped += 1
            notes.append(f'{record.get("case_id")}: not in corpus {corpus_name}')
            continue
        ids = member_ids(case, record)
        if ids is None:
            skipped += 1
            notes.append(f"{case.id}: no id map (a mid-case add_member) — left alone")
            continue
        before = ((record.get("grades") or {}).get("tool_selection") or {}).get("passed")
        verdict = graders.grade_tool_selection(corpus.resolve_args(case, ids), record)
        record.setdefault("grades", {})["tool_selection"] = {
            "passed": verdict.passed, "reason": verdict.reason}
        if verdict.passed is not before:
            changed += 1
            notes.append(f"{case.id}#{record.get('rep')}: {before} → {verdict.passed} "
                         f"({verdict.reason[:80]})")

    results["regraded_tool_selection"] = True
    return changed, skipped, notes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench.regrade", description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="defaults to overwriting the input file")
    args = parser.parse_args(argv)

    results = json.loads(args.results.read_text(encoding="utf-8"))
    changed, skipped, notes = regrade(results, args.corpus)
    (args.out or args.results).write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"regraded tool_selection: {changed} verdict(s) changed, {skipped} skipped")
    for note in notes:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
