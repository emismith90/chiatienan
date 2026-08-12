"""Replay a corpus through the engine and grade every turn.

    python -m bench.run --corpus {meals,week,prod,bills,all} \
        --engine {cursor,pi} --repeat N --out PATH

Three properties this runner has to have, each because the alternative quietly
lies:

* **Every case runs in its own reconstructed world** (`bench.world`). Replaying
  history as chat text creates no ledger rows, so the money graders would fail on
  both engines and `--compare` would call that "no change".
* **Grading happens here, while the world is alive.** `grade_ledger_state` reads
  the database, and the per-case database is gone by the time a report is
  rendered. So a record carries its verdicts, and `bench.report` only aggregates.
* **One case failing never kills the run.** A benchmark that stops at the first
  error cannot report honestly, and the first error is usually the least
  interesting one.

`--repeat` defaults to 3 because both engines are nondeterministic and they are
different models: with one run per engine a verdict flip is indistinguishable
from sampling variance (design §11.2).
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

from bench import corpus, graders
from bench.world import build_world, frozen_clock

#: Kept in the record so a stale results file cannot be silently misread.
RECORD_VERSION = 1


def _invocation_dicts(result) -> list[dict]:
    """`TurnResult.tools` → plain dicts the graders and JSON both accept."""
    out = []
    for inv in getattr(result, "tools", None) or []:
        out.append({"name": getattr(inv, "name", None),
                    "args": getattr(inv, "args", None),
                    "result": getattr(inv, "result", None)})
    return out


def _grade(case, record: dict, db, ids: dict, judge=None) -> dict:
    """Run all three pass/fail graders, each isolated from the others' failures."""
    resolved = corpus.resolve_args(case, ids)
    graded = {}
    for name, call in (
        ("tool_selection", lambda: graders.grade_tool_selection(resolved, record)),
        ("ledger_state", lambda: graders.grade_ledger_state(case, record, db, ids)),
        ("prose_quality", lambda: graders.grade_prose(case, record, judge=judge)),
    ):
        try:
            verdict = call()
            graded[name] = {"passed": verdict.passed, "reason": verdict.reason}
        except Exception as exc:  # a broken grader must not lose the whole record
            graded[name] = {"passed": None, "reason": f"grader raised: {exc!r}"}
    return graded


def _run_one(case, rep: int, run_turn, judge=None) -> dict:
    """One case, one repetition: fresh database, rebuilt world, one LLM turn."""
    from app import tools
    from app.db import Database

    record = {"version": RECORD_VERSION, "case_id": case.id, "rep": rep,
              "source": case.source, "day": case.day, "message": case.message,
              "had_images": case.had_images, "room_id": None, "tools": [],
              "final_text": "", "error": None, "elapsed_s": 0.0, "stats": None,
              "grades": {}}

    with tempfile.TemporaryDirectory(prefix=f"bench-{case.id}-") as tmp:
        db = Database(f"sqlite:///{tmp}/bench.db")
        db.create_all()
        started = time.monotonic()
        try:
            room_id, ids, _ = build_world(db, case)
            record["room_id"] = room_id
            ctx = tools.ToolContext(db=db, room_id=room_id,
                                    sender_member_id=ids.get(case.actor),
                                    turn_mentions=[])
            with frozen_clock(case.day):
                result = run_turn(case.message, ctx, images=case.images or None)
                if inspect.isawaitable(result):
                    result = asyncio.run(_await(result))
                record["elapsed_s"] = time.monotonic() - started
                record["tools"] = _invocation_dicts(result)
                record["final_text"] = getattr(result, "final_text", "") or ""
                record["error"] = getattr(result, "error", None)
                record["stats"] = getattr(result, "stats", None)
                # Graded inside the frozen clock: `balances_by_member` resolves
                # the open period from `today_ict`.
                record["grades"] = _grade(case, record, db, ids, judge=judge)
        except Exception as exc:
            # One bad case is a data point, not the end of the run.
            record["elapsed_s"] = record["elapsed_s"] or time.monotonic() - started
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["traceback"] = traceback.format_exc(limit=6)
            try:
                record["grades"] = _grade(case, record, db, ids, judge=judge)
            except Exception:
                record["grades"] = {}
    return record


async def _await(awaitable):
    return await awaitable


def run_corpus(name: str, *, repeat: int = 3, run_turn=None, judge=None,
               on_record=None) -> list[dict]:
    """Replay every case in `name`, `repeat` times each, and return the records.

    `run_turn` is injected so the tests can drive this offline; production passes
    `app.agent.run_turn`. Its signature is the frozen one —
    `run_turn(user_text, ctx, images=…)` — and it may be sync or async.
    """
    if run_turn is None:
        from app.agent import run_turn as real_run_turn
        run_turn = real_run_turn

    records = []
    for case in corpus.load(name):
        for rep in range(repeat):
            record = _run_one(case, rep, run_turn, judge=judge)
            records.append(record)
            if on_record:
                on_record(record)
    return records


def _assert_engine_matches_the_tree(engine: str) -> None:
    """Fail a mislabelled run rather than writing Cursor results as Pi's.

    There is only ever one engine in the tree — the cutover is hard, not
    flag-gated — so `--engine` is a claim to check, not a switch to honour.
    """
    import importlib.util
    has_cursor = importlib.util.find_spec("cursor_sdk") is not None
    has_sidecar = (Path(__file__).resolve().parent.parent / "agent_sidecar").is_dir()
    actual = "cursor" if has_cursor else ("pi" if has_sidecar else None)
    if actual is None:
        raise SystemExit("cannot tell which engine this tree has: neither cursor_sdk "
                         "is importable nor agent_sidecar/ present")
    if actual != engine:
        raise SystemExit(f"--engine {engine} but this tree is {actual} "
                         f"(cursor_sdk importable={has_cursor}, agent_sidecar={has_sidecar})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench.run", description=__doc__)
    parser.add_argument("--corpus", required=True,
                        choices=["meals", "week", "prod", "bills", "all"])
    parser.add_argument("--engine", required=True, choices=["cursor", "pi"])
    parser.add_argument("--repeat", type=int, default=3,
                        help="runs per case (default 3; one run cannot separate a "
                             "regression from sampling noise)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--judge-model", default=None,
                        help="pin the prose judge; without it prose_quality is n/a")
    args = parser.parse_args(argv)

    _assert_engine_matches_the_tree(args.engine)

    judge = None
    if args.judge_model:
        from bench.judge import openrouter_judge
        judge = openrouter_judge(args.judge_model)

    cases = corpus.load(args.corpus)
    if not cases:
        print(f"corpus {args.corpus!r} is empty — nothing to run", file=sys.stderr)
        return 1
    print(f"{len(cases)} case(s) × {args.repeat} → {len(cases) * args.repeat} turns",
          file=sys.stderr)

    def progress(record):
        marks = "".join({True: "+", False: "x", None: "."}[record["grades"].get(k, {}).get("passed")]
                        for k in ("tool_selection", "ledger_state", "prose_quality"))
        print(f'  {record["case_id"]}#{record["rep"]} {marks} '
              f'{record["elapsed_s"]:.1f}s {record["error"] or ""}', file=sys.stderr)

    records = run_corpus(args.corpus, repeat=args.repeat, judge=judge, on_record=progress)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "version": RECORD_VERSION, "engine": args.engine, "corpus": args.corpus,
        "repeat": args.repeat, "judge_model": args.judge_model,
        "records": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
