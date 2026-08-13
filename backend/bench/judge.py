"""The prose judge: one pinned model, called over OpenRouter, engine-independent.

`grade_prose` takes its judge as an argument and never builds one, so the graders
stay offline and testable. This is where the real one is built.

**Deliberately not the engine under test.** The judge has to be pinned across the
Cursor baseline *and* the Pi run — a baseline graded with no judge, or a different
one, is not a comparison (design §11.5). Routing it through OpenRouter rather than
through whichever engine is in the tree means the same `BENCH_JUDGE_MODEL` answers
both runs, before and after the cutover.

`urllib` rather than a client library: this is one POST, and `bench/` must not add
a production dependency for a development tool.

The key lives in **`OPEN_ROUTER_KEY`**. That is the name the environment actually
uses; design §10's `OPENROUTER_API_KEY` was an assumption and is wrong. One
canonical name, no aliases — the sidecar (Task 13) needs the same variable, and a
second accepted spelling is how half a deployment ends up unauthenticated.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: The environment's actual name for the OpenRouter credential.
KEY_ENV = "OPEN_ROUTER_KEY"

#: Roughly two sentences of reply. The rubric asks for JSON, not an essay.
MAX_TOKENS = 200


def _post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed host
        return json.loads(response.read().decode())


def _extract_json(text: str) -> dict | None:
    """Pull the verdict object out of a reply that may be fenced or chatty."""
    for candidate in (text, *re.findall(r"\{.*?\}", text or "", re.DOTALL)):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and "ok" in parsed:
            return parsed
    return None


def build_prompt(case, record: dict, rubric: str) -> str:
    """The judge sees the user's message and the reply — nothing else.

    Not the expected tools, not the golden numbers: the question is whether this
    reply is a good reply, and showing the answer key invites the judge to grade
    correctness it is not being asked about.
    """
    return (f"{rubric}\n"
            f"--- Người dùng ---\n{case.message}\n"
            f"--- Bot ---\n{record.get('final_text') or ''}\n")


def openrouter_judge(model: str, *, api_key: str | None = None, post=_post,
                     timeout: float = 60.0):
    """Return a `judge(case, record, rubric)` callable for `grade_prose`.

    Any failure — no key, a transport error, an unparseable reply — returns a
    dict **without** `ok`, which `grade_prose` records as *not graded*. That is
    deliberate: a judge that silently passed on error would turn an outage into a
    clean bill of health, and a judge that failed on error would blame the engine
    for the harness's own problem.
    """
    key = api_key if api_key is not None else os.environ.get(KEY_ENV, "")

    def judge(case, record, rubric):
        if not key:
            return {"error": f"{KEY_ENV} is not set"}
        payload = {"model": model, "max_tokens": MAX_TOKENS, "temperature": 0,
                   "messages": [{"role": "user",
                                 "content": build_prompt(case, record, rubric)}]}
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            body = post(OPENROUTER_URL, payload, headers, timeout)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return {"error": f"judge transport failed: {exc}"}
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return {"error": f"judge returned an unexpected body: {body!r}"}
        verdict = _extract_json(text)
        return verdict if verdict is not None else {"error": f"judge said: {text!r}"}

    judge.model = model
    return judge


# --------------------------------------------------------------------------- #
# The agent as judge
# --------------------------------------------------------------------------- #
#
# `openrouter_judge` above is the unattended path. It is not the *best* path, and
# the first baseline showed why: `gemini-2.5-flash-lite` failed correct replies for
# "restating an amount already present in the user's message" when the message
# contained no amount at all. A flash-tier judge is the weakest link in a chain
# whose whole purpose is careful reading.
#
# So the judge can also be **the agent running the harness** — read the replies,
# apply the rubric, write the verdicts. That is strictly better judgment, and it
# needs no key and no third-party model. The cost is that Python cannot call the
# agent, so the exchange is a file: `--prepare` writes what needs judging,
# `--apply` merges the verdicts back and records who judged.
#
# `§11.5`'s requirement survives intact — the judge must be *pinned across both
# runs*. With an agent judge that means grading the baseline and the Pi run in one
# sitting against one rubric, and `judge_model` records the identity so a
# mismatched pair is still caught by `bench.report`.

def prepare_batch(results: dict, corpus: dict | None = None) -> list[dict]:
    """Everything in `results` still awaiting a prose verdict.

    Only records that reached the judge are included: a card turn, an errored turn
    and a stage-1 failure are already decided, and re-judging them would let an
    opinion overrule `moneyguard`.

    **The record's own `final_text` is what gets judged.** `--corpus` exists for the
    recorded baseline, whose bodies were stripped before it was committed — but its
    `reply` field is *production's* text, and preferring it meant a Pi run was
    handed prod's replies to grade. Twenty of them, verbatim, with narration prod
    was being replaced for ("mình đọc skill phù hợp rồi xử lý"). The corpus is now
    the fallback, never the override.
    """
    bodies = {}
    for case in (corpus or {}).get("cases") or []:
        bodies[case["id"]] = (case.get("message") or "", case.get("reply") or "")

    batch = []
    for record in results.get("records") or []:
        verdict = (record.get("grades") or {}).get("prose_quality") or {}
        if verdict.get("passed") is not None:
            continue
        if not str(verdict.get("reason", "")).startswith("not graded: no judge"):
            # "the room saw a card", "empty reply", a stage-1 failure — all decided.
            continue
        message, reply = bodies.get(record["case_id"], ("", ""))
        batch.append({
            "case_id": record["case_id"],
            "rep": record.get("rep", 0),
            "message": record.get("message") or message or "",
            "reply": record.get("final_text") or reply or "",
        })
    return batch


def apply_verdicts(results: dict, verdicts: dict, judged_by: str) -> tuple[int, int]:
    """Merge `{case_id: {"ok": bool, "reason": str}}` into `results`.

    Returns `(applied, unmatched)`. Records the judge's identity on the run, so a
    comparison against a differently-judged run still trips `bench.report`'s
    warning.
    """
    applied = 0
    for record in results.get("records") or []:
        verdict = verdicts.get(record["case_id"])
        if verdict is None:
            continue
        grades = record.setdefault("grades", {})
        current = grades.get("prose_quality") or {}
        if current.get("passed") is not None:
            continue          # never overrule a decided verdict
        grades["prose_quality"] = {"passed": bool(verdict["ok"]),
                                   "reason": str(verdict.get("reason") or "")}
        applied += 1
    results["judge_model"] = judged_by
    unmatched = len(set(verdicts) - {r["case_id"] for r in results.get("records") or []})
    return applied, unmatched


def _main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="bench.judge", description=__doc__)
    parser.add_argument("--prepare", type=Path, metavar="RESULTS",
                        help="write the records awaiting a prose verdict")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="corpus JSON, for the message/reply bodies")
    parser.add_argument("--apply", nargs=2, type=Path, metavar=("RESULTS", "VERDICTS"),
                        help="merge agent-written verdicts into a results file")
    parser.add_argument("--judged-by", default="claude-opus-5 (agent)",
                        help="recorded as judge_model; pin it across compared runs")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.prepare:
        results = json.loads(args.prepare.read_text(encoding="utf-8"))
        corpus = json.loads(args.corpus.read_text(encoding="utf-8")) if args.corpus else None
        batch = prepare_batch(results, corpus)
        from bench.graders import PROSE_RUBRIC
        payload = json.dumps({"rubric": PROSE_RUBRIC, "cases": batch},
                             ensure_ascii=False, indent=2)
        (args.out.write_text(payload, encoding="utf-8") if args.out else print(payload))
        print(f"{len(batch)} case(s) awaiting a verdict", file=sys.stderr)
        return 0

    if args.apply:
        results_path, verdicts_path = args.apply
        results = json.loads(results_path.read_text(encoding="utf-8"))
        raw = json.loads(verdicts_path.read_text(encoding="utf-8"))
        verdicts = raw.get("verdicts", raw)
        applied, unmatched = apply_verdicts(results, verdicts, args.judged_by)
        results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"applied {applied} verdict(s), {unmatched} unmatched, "
              f"judge_model={args.judged_by!r}", file=sys.stderr)
        return 1 if unmatched else 0

    parser.error("give --prepare RESULTS or --apply RESULTS VERDICTS")


if __name__ == "__main__":
    raise SystemExit(_main())
