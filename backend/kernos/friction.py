"""Deterministic friction detectors over stored turn summaries (plan Phase 10.1).

What went wrong in a space, decided by code rather than by a model reading prose. Every
detector reads only the **summary** a trace row carries (``kernos.plugins.after.summarize``),
never the full trace: a summary is one JSON column, so scanning fifty turns is one query
and no tool results are loaded. That matters twice — cost, and safety: the full trace
carries past tool results, and the less of that reaches a model the better (Phase 8
review F1).

A finding is a count, a share, and up to three example turns. It never says *why* in the
model's voice; ``what`` and ``suggests`` are fixed strings the steward reads, so two runs
over the same traces produce the same report.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

#: A turn slower than this, that was not cut short, is friction the room felt.
SLOW_MS = 60_000

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Detector:
    """``match(summary) -> reason | None``. The reason is shown with the example turn."""

    id: str
    severity: str
    what: str
    suggests: str
    match: Callable[[dict], str | None]


def _verdicts(summary: dict) -> list[dict]:
    return [v for v in (summary.get("verdicts") or []) if isinstance(v, dict)]


def _blocked_by(summary: dict, plugin: str) -> str | None:
    for v in _verdicts(summary):
        if v.get("plugin") == plugin and v.get("outcome") == "block":
            return v.get("reason") or plugin
    return None


def _fabricated(summary: dict) -> str | None:
    return _blocked_by(summary, "app.validate.fabricated_commit")


def _run_error(summary: dict) -> str | None:
    return summary.get("error") or None


def _rule_blocked(summary: dict) -> str | None:
    for v in _verdicts(summary):
        # every block that is not the forgery guard: a profile's validation rule refused a
        # tool call (chips not conserved, total ≠ items, a negative amount…)
        if v.get("outcome") == "block" and v.get("plugin") != "app.validate.fabricated_commit":
            return f"{v.get('plugin')}: {v.get('reason') or 'blocked'}"
    return None


def _unbacked(summary: dict) -> str | None:
    for v in _verdicts(summary):
        if v.get("plugin") == "app.validate.unbacked_amounts" and v.get("outcome") == "warn":
            return v.get("reason") or "unbacked amounts"
    return None


def _capped(summary: dict) -> str | None:
    if not summary.get("capped"):
        return None
    return f"cut short after {round((summary.get('elapsed_ms') or 0) / 1000, 1)}s"


def _slow(summary: dict) -> str | None:
    ms = summary.get("elapsed_ms") or 0
    if summary.get("capped") or ms < SLOW_MS:
        return None
    return f"{round(ms / 1000, 1)}s"


DETECTORS: tuple[Detector, ...] = (
    Detector(
        "fabricated_commit", "high",
        "The reply claimed the ledger was written when no tool wrote it. The message was "
        "replaced before the room saw it, so nothing was lost — but the model tried.",
        "Read the turns. If they share a shape (a bill photo with no names, a retold meal), "
        "the record-meal skill or the prompt is what needs the change.",
        _fabricated),
    Detector(
        "run_error", "high",
        "The turn broke and the room got an error instead of an answer.",
        "Check whether the errors name one tool or one model. A provider error is not a "
        "content problem and no prompt change will fix it.",
        _run_error),
    Detector(
        "rule_blocked", "medium",
        "A validation rule refused a tool call — the numbers did not add up, so nothing "
        "was written. The room saw a question instead of a card.",
        "If the same rule fires often, the model is being asked for something it keeps "
        "getting wrong; the skill that describes that tool is the place to fix it.",
        _rule_blocked),
    Detector(
        "unbacked_amounts", "medium",
        "The reply contained money that no tool produced this turn. Often it is a number "
        "read off a bill photo (correct, but untraceable), sometimes it is invented.",
        "Look at whether the turns had images. If they did not, the model is doing "
        "arithmetic in prose and the money-safety rule needs to be firmer.",
        _unbacked),
    Detector(
        "capped", "medium",
        "The turn hit its time or tool-call limit and was cut short, so the answer may be "
        "missing or empty. Nothing was recorded.",
        "Repeated caps on similar questions mean the turn is doing too much. A skill that "
        "names fewer steps, or a higher cap, are the two levers.",
        _capped),
    Detector(
        "slow", "low",
        f"The turn took over {SLOW_MS // 1000}s but still finished. The room waited.",
        "Only worth acting on in volume, and usually a model or tool-count question rather "
        "than a content one.",
        _slow),
)


def detect(summaries: list[dict], *, examples: int = 3) -> list[dict]:
    """Findings over the turn summaries, worst first; a detector that matched nothing is
    left out. ``summaries`` are ``{turn_id, summary}`` pairs as ``report`` builds them."""
    findings = []
    total = len(summaries) or 1
    for detector in DETECTORS:
        hits = []
        for row in summaries:
            reason = detector.match(row.get("summary") or {})
            if reason:
                hits.append({"turn_id": row.get("turn_id"), "reason": reason})
        if not hits:
            continue
        findings.append({
            "id": detector.id, "severity": detector.severity, "count": len(hits),
            "share": round(len(hits) / total, 3), "what": detector.what,
            "suggests": detector.suggests, "examples": hits[:examples],
        })
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], -f["count"]))
    return findings


def report(traces: list[dict], *, examples: int = 3) -> dict:
    """``{scanned, from, to, findings, clean}`` over trace rows as ``TraceStore.list``
    returns them (summaries only). ``clean`` is true when nothing matched — a steward that
    reads this should stop rather than invent work."""
    rows = [{"turn_id": t.get("turn_id") or t.get("id"), "summary": t.get("summary") or {}} for t in traces]
    findings = detect(rows, examples=examples)
    starts = sorted(t.get("started") for t in traces if t.get("started"))
    return {
        "scanned": len(rows),
        "from": starts[0] if starts else None,
        "to": starts[-1] if starts else None,
        "findings": findings,
        "clean": not findings,
    }
