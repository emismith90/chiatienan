"""Render a run as markdown, and compare two runs by pass rate.

    python -m bench.report RESULTS.json
    python -m bench.report --compare BASE.json NEW.json

**The unit of comparison is a pass rate, not a verdict.** `--repeat` produces N
records per case because both engines are nondeterministic and they are different
models; with one run each, a verdict flip is indistinguishable from sampling
variance (design §11.2).

Three things this module refuses to let a report imply:

* **A silently dropped case is not "no change".** A case in one run and not the
  other gets an explicit `MISSING` row and blocks the ship criterion.
* **Two runs that both failed a case did not agree.** They certified nothing, so
  a `0.00` delta would be a lie; that gets a `BOTH-FAILING` row and blocks too.
* **Ungraded is not passed.** A `passed is None` repetition is excluded from the
  denominator and renders as `n/a`. A whole case that is `n/a` never contributes
  to any rate — least of all to a favourable one.

**The reference is not a ceiling.** Equivalence is the bar for *money* — the tool
chosen and the amounts in it (`MONEY_GRADERS`), where design D3 says behavior must
not change. `prose_quality`, latency and cost are **quality** measures graded
against a rubric, not against what the previous engine happened to do. So a case
the reference itself failed is flagged `BASELINE-FAILED-TOO` rather than
`BOTH-FAILING`, and beating a failing reference is flagged `IMPROVED`. The old
engine's mistakes are not a specification to reproduce.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bench.graders import summarize_cost_latency

GRADERS = ("tool_selection", "ledger_state", "prose_quality")

#: The graders the ship criterion is expressed over. A `prose_quality` change has
#: to be "understood and written down", not auto-blocking.
MONEY_GRADERS = ("tool_selection", "ledger_state")

#: "no case whose tool_selection or ledger_state pass rate dropped by more than
#: 1/3". Strictly more than, so a 3/3 → 2/3 dip is a note rather than a block.
MAX_DROP = 1 / 3


def load_results(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def case_rates(results: dict) -> dict[str, dict[str, tuple[int, int]]]:
    """`{case_id: {grader: (passed, graded)}}`.

    `graded` counts only repetitions whose verdict was `True` or `False`. An
    ungraded repetition is neither a pass nor a failure, so folding it in either
    direction would misreport the run.
    """
    rates: dict[str, dict[str, tuple[int, int]]] = {}
    for record in results.get("records") or []:
        case = rates.setdefault(record["case_id"], {g: (0, 0) for g in GRADERS})
        for grader in GRADERS:
            verdict = (record.get("grades") or {}).get(grader) or {}
            if verdict.get("passed") is None:
                continue
            passed, graded = case[grader]
            case[grader] = (passed + (1 if verdict["passed"] else 0), graded + 1)
    return rates


def format_rate(rate: tuple[int, int]) -> str:
    passed, graded = rate
    return f"{passed}/{graded}" if graded else "n/a"


def _fraction(rate: tuple[int, int]) -> float | None:
    passed, graded = rate
    return passed / graded if graded else None


def _case_source(results: dict) -> dict[str, str]:
    return {r["case_id"]: r.get("source", "?") for r in results.get("records") or []}


def _grader_totals(rates: dict) -> dict[str, tuple[int, int]]:
    totals = {}
    for grader in GRADERS:
        passed = sum(r[grader][0] for r in rates.values())
        graded = sum(r[grader][1] for r in rates.values())
        totals[grader] = (passed, graded)
    return totals


def _cost_line(summary: dict) -> str:
    if summary["total_cost_usd"] is None:
        return "**cost** unknown (the engine reported no stats)"
    return (f'**cost** ${summary["total_cost_usd"]:.4f} over '
            f'{summary["total_tokens"]:,} tokens '
            f'({summary["stats_n"]}/{summary["n"]} turns reported stats)')


def render(results: dict) -> str:
    """A per-case grid, per-grader totals, latency, cost, and every error."""
    rates = case_rates(results)
    sources = _case_source(results)
    records = results.get("records") or []
    summary = summarize_cost_latency(records)

    lines = [
        f'# Benchmark — `{results.get("engine")}` on corpus `{results.get("corpus")}`',
        "",
        f'- **repeat** {results.get("repeat")} · **turns** {summary["n"]} · '
        f'**errored** {summary["error_n"]}',
    ]
    judge = results.get("judge_model")
    lines.append(f"- **judge** `{judge}`" if judge else
                 "- **judge** none configured — `prose_quality` is **n/a** for this "
                 "whole run, and a run with no judge cannot be compared against one "
                 "that had one (design §11.5)")
    lines += [
        f'- **latency** p50 {summary["p50_s"]:.1f}s · p95 {summary["p95_s"]:.1f}s · '
        f'**mean tool calls** {summary["mean_tool_calls"]:.1f}'
        if summary["n"] else "- **latency** n/a",
        f"- {_cost_line(summary)}",
        "",
        "## Per case",
        "",
        "| case | source | " + " | ".join(GRADERS) + " |",
        "|---|---|" + "---|" * len(GRADERS),
    ]
    for case_id in sorted(rates, key=lambda c: (sources.get(c, ""), c)):
        cells = " | ".join(format_rate(rates[case_id][g]) for g in GRADERS)
        lines.append(f'| `{case_id}` | {sources.get(case_id, "?")} | {cells} |')

    lines += ["", "## Per grader", "", "| grader | passed | graded | rate |", "|---|---|---|---|"]
    for grader, (passed, graded) in _grader_totals(rates).items():
        rate = f"{passed / graded:.2f}" if graded else "n/a"
        lines.append(f"| {grader} | {passed} | {graded} | {rate} |")

    errored = [r for r in records if r.get("error")]
    if errored:
        lines += ["", "## Errors", ""]
        for record in errored:
            lines.append(f'- `{record["case_id"]}#{record["rep"]}` — {record["error"]}')

    ungraded = [c for c in rates if all(rates[c][g][1] == 0 for g in GRADERS)]
    if ungraded:
        lines += ["", "## Graded nothing", "",
                  "These cases produced no verdict at all, so they certify nothing:",
                  "", ", ".join(f"`{c}`" for c in sorted(ungraded))]
    return "\n".join(lines) + "\n"


def ship_blockers(base: dict, new: dict) -> list[dict]:
    """Everything that fails the ship criterion, as structured rows.

    Blocks on a money-grader pass-rate drop of **more than** 1/3, on a case
    present in one run and not the other, and on a case both runs failed — the
    last because a case that failed on both engines compared nothing, however
    tidy its `0.00` delta looks.
    """
    base_rates, new_rates = case_rates(base), case_rates(new)
    blockers = []

    for case_id in sorted(set(base_rates) | set(new_rates)):
        if case_id not in new_rates or case_id not in base_rates:
            blockers.append({"case_id": case_id, "grader": "-", "kind": "MISSING",
                             "detail": f'absent from the {"new" if case_id not in new_rates else "base"} run'})
            continue
        for grader in MONEY_GRADERS:
            before, after = _fraction(base_rates[case_id][grader]), _fraction(new_rates[case_id][grader])
            if before is None or after is None:
                continue
            if before == 0.0 and after == 0.0:
                blockers.append({"case_id": case_id, "grader": grader,
                                 "kind": "BOTH-FAILING",
                                 "detail": "0 on both runs — this case certified nothing"})
            elif before - after > MAX_DROP + 1e-9:
                blockers.append({"case_id": case_id, "grader": grader, "kind": "DROP",
                                 "detail": f"{before:.2f} → {after:.2f} ({after - before:+.2f})"})
    return blockers


def render_compare(base: dict, new: dict) -> str:
    """A pass-rate diff, with `MISSING` and `BOTH-FAILING` called out by name."""
    base_rates, new_rates = case_rates(base), case_rates(new)
    sources = {**_case_source(base), **_case_source(new)}
    base_engine, new_engine = base.get("engine"), new.get("engine")

    lines = [f"# `{base_engine}` → `{new_engine}` equivalence", ""]

    if base.get("baseline_kind") == "recorded-prod-log":
        lines += [
            "> ⚠️ **The base run is a recorded production log, not a measured "
            "engine run.** Its expectations were derived from the same rows as its "
            "records, so its `tool_selection` rate is **1.0 by construction** and "
            "says nothing about the graders. Read the new run's **absolute** rate "
            "as the equivalence number, not the delta — and note the base side has "
            "`repeat=1`, since a log cannot be re-rolled.", ""]

    if base.get("judge_model") != new.get("judge_model"):
        lines += [
            f'> ⚠️ **The judge differs between these runs** — base '
            f'`{base.get("judge_model")}`, new `{new.get("judge_model")}`. The '
            "`prose_quality` column below is **not comparable**; a baseline graded "
            "with no judge (or a different one) against a run graded with one is not "
            "a comparison (design §11.5).", ""]

    lines += ["## Pass rate by case", "",
              "| case | source | " + " | ".join(f"{g} Δ" for g in GRADERS) + " | flag |",
              "|---|---|" + "---|" * (len(GRADERS) + 1)]

    for case_id in sorted(set(base_rates) | set(new_rates),
                          key=lambda c: (sources.get(c, ""), c)):
        if case_id not in base_rates or case_id not in new_rates:
            side = "new" if case_id not in new_rates else "base"
            lines.append(f'| `{case_id}` | {sources.get(case_id, "?")} | '
                         + " | ".join(["—"] * len(GRADERS))
                         + f" | **MISSING** from the {side} run |")
            continue

        cells, flags = [], []
        for grader in GRADERS:
            before = _fraction(base_rates[case_id][grader])
            after = _fraction(new_rates[case_id][grader])
            if before is None or after is None:
                cells.append("n/a")
                continue
            cells.append(f'{format_rate(base_rates[case_id][grader])} → '
                         f'{format_rate(new_rates[case_id][grader])} '
                         f'({after - before:+.2f})')
            if before == 0.0 and after == 0.0:
                # For money, agreeing on zero means the case certified nothing. For
                # a quality grader it means the reference itself did not clear the
                # bar — the bar is the rubric, not what the old engine managed.
                flags.append(f"**BOTH-FAILING** ({grader})" if grader in MONEY_GRADERS
                             else f"BASELINE-FAILED-TOO ({grader})")
            elif before == 0.0 and after > 0.0:
                # Better is better. An improvement over a reference that failed is
                # a win, not a difference to explain away.
                flags.append(f"**IMPROVED** ({grader})")
        lines.append(f'| `{case_id}` | {sources.get(case_id, "?")} | '
                     + " | ".join(cells) + f' | {" ".join(flags) or ""} |')

    base_summary = summarize_cost_latency(base.get("records") or [])
    new_summary = summarize_cost_latency(new.get("records") or [])
    lines += ["", "## Latency and cost", "",
              "| metric | " + f"{base_engine} | {new_engine} | Δ |", "|---|---|---|---|"]
    for label, key in (("p50 s", "p50_s"), ("p95 s", "p95_s"),
                       ("mean tool calls", "mean_tool_calls")):
        before, after = base_summary[key], new_summary[key]
        delta = f"{after - before:+.2f}" if before is not None and after is not None else "—"
        lines.append(f'| {label} | {before if before is None else f"{before:.2f}"} '
                     f'| {after if after is None else f"{after:.2f}"} | {delta} |')
    lines.append(f'| total cost USD | {base_summary["total_cost_usd"] or "unknown"} '
                 f'| {new_summary["total_cost_usd"] or "unknown"} | — |')

    blockers = ship_blockers(base, new)
    lines += ["", "## Ship criterion", "",
              f"No case whose `{'` or `'.join(MONEY_GRADERS)}` pass rate dropped by "
              "more than 1/3; no `MISSING` case; no `BOTH-FAILING` case.", ""]
    if not blockers:
        lines.append("**No blockers.** Read the prose and latency columns before shipping "
                     "anyway — the criterion covers money, not everything.")
    else:
        lines.append(f"**{len(blockers)} blocker(s):**")
        lines.append("")
        for blocker in blockers:
            lines.append(f'- `{blocker["case_id"]}` · {blocker["grader"]} · '
                         f'**{blocker["kind"]}** — {blocker["detail"]}')
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench.report", description=__doc__)
    parser.add_argument("results", nargs="?", help="a results JSON to render")
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "NEW"),
                        help="diff two runs by pass rate")
    args = parser.parse_args(argv)

    if args.compare:
        print(render_compare(load_results(args.compare[0]), load_results(args.compare[1])))
        return 0
    if not args.results:
        parser.error("give a results file, or --compare BASE NEW")
    print(render(load_results(args.results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
