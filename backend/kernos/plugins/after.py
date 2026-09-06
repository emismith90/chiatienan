"""``kernos.after.trace``: the turn trace, persisted (design §8.6; plan Task 4.1).

The trace **is** the log: which plugins ran and what each decided (``ctx.trace``),
every tool call with its arguments and result (the eval record's shape, so a turn
becomes an eval case by copying), and a summary the admin timeline reads. Written
from the ``after`` stage, which the pipeline runs in a ``finally`` — a turn that
raised is traced with its error, with ``turn_id`` null when it never reached the
engine. Retention is ``keep_days`` (rows older than that are pruned on write).
Tool results can carry personal data (a settlement's ``qr_url``); retention is the
control.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from kernos.adapters.protocols import TraceStore
from kernos.kernel.context import Body, Draft, Stage, TurnContext
from kernos.kernel.plugin import BasePlugin


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def summarize(ctx: TurnContext) -> dict:
    """The summary row. Tolerates a turn that never produced a result or an outcome."""
    result = ctx.result
    stats = (getattr(result, "stats", None) or {}) if result is not None else {}
    trace_ms = sum(float(t.get("ms") or 0) for t in ctx.trace)
    errors = [t["error"] for t in ctx.trace if t.get("outcome") == "error" and t.get("error")]
    outcome: dict[str, Any] | None = None
    if isinstance(ctx.outcome, Draft):
        outcome = {"kind": "draft", "draft_kind": ctx.outcome.kind}
    elif isinstance(ctx.outcome, Body):
        outcome = {"kind": "body", "claimed_by_pack": bool(ctx.outcome.claimed_by_pack),
                   "attachment_type": (ctx.outcome.attachments or {}).get("type")}
    return {
        "principal": str(ctx.principal.id) if ctx.principal is not None else None,
        "text_chars": len(ctx.text or ""),
        "images": len(ctx.images or []),
        "model": ctx.model,
        "tools": [inv.name for inv in (getattr(result, "tools", None) or [])],
        "tokens": stats.get("tokens"),
        "cost": stats.get("cost"),
        "elapsed_ms": round(trace_ms, 1),
        "capped": bool(getattr(result, "capped", False)) if result is not None else False,
        "error": (getattr(result, "error", None) if result is not None else None) or (errors[0] if errors else None),
        "outcome": outcome,
        "verdicts": [{"plugin": t["plugin"], "outcome": t["outcome"], "reason": t.get("reason")}
                     for t in ctx.trace if t.get("outcome") in ("warn", "block")],
        "stopped": bool(ctx.stopped),
        "depth": ctx.depth,
    }


def tool_calls(ctx: TurnContext) -> list[dict]:
    """``[{name, args, result, from_agent}]`` — the eval record's ``tools`` shape."""
    return [{"name": inv.name, "args": inv.args, "result": inv.result,
             "from_agent": getattr(inv, "from_agent", None)}
            for inv in (getattr(ctx.result, "tools", None) or [])]


class Trace(BasePlugin):
    id, version, stage = "kernos.after.trace", "1", Stage.after
    config_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"keep_days": {"type": "integer", "minimum": 1, "default": 30}},
    }

    def __init__(self, traces: TraceStore, *, now=None) -> None:
        self._traces = traces
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def run(self, ctx: TurnContext, config: dict) -> None:
        finished = self._now()
        summary = summarize(ctx)
        started = finished - timedelta(milliseconds=summary["elapsed_ms"])
        turn_id = ctx.turn_id or getattr(ctx.result, "turn_id", None)
        row = self._traces.write(
            ctx.space_id, turn_id, started=_utc(started), finished=_utc(finished),
            summary=summary, tools=tool_calls(ctx), trace=list(ctx.trace),
            keep_days=config.get("keep_days", 30))
        ctx.extras["trace_row_id"] = row["id"] if isinstance(row, dict) else getattr(row, "id", None)
