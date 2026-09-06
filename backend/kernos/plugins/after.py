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
    # a sub-agent's rows joined this trace as a span; its time is inside the manager's run
    # stage already and its plugin errors are its own (Phase 7 review F6)
    own = [t for t in ctx.trace if "span" not in t]
    trace_ms = sum(float(t.get("ms") or 0) for t in own)
    errors = [t["error"] for t in own if t.get("outcome") == "error" and t.get("error")]
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
        "tools": [f"{inv.from_agent}:{inv.name}" if getattr(inv, "from_agent", None) else inv.name
                  for inv in (getattr(result, "tools", None) or [])],
        "tokens": stats.get("tokens"),
        "cost": stats.get("cost"),
        "elapsed_ms": round(trace_ms, 1),
        "capped": bool(getattr(result, "capped", False)) if result is not None else False,
        "error": (getattr(result, "error", None) if result is not None else None) or (errors[0] if errors else None),
        "outcome": outcome,
        "verdicts": [{"plugin": t["plugin"], "outcome": t["outcome"], "reason": t.get("reason"),
                      **({"span": t["span"]} if "span" in t else {})}
                     for t in ctx.trace if t.get("outcome") in ("warn", "block")],
        "stopped": bool(ctx.stopped),
        "depth": ctx.depth,
        **({"agent_log": list(ctx.extras["agent_log"])} if ctx.extras.get("agent_log") else {}),   # cms_log lines (Phase 8)
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


#: Argument names whose values are member ids — rewritten to keys on capture. The
#: rest of an argument set is left alone (an amount equal to a member id must not
#: become a key).
DEFAULT_MEMBER_ARGS = ("payer", "from", "to", "participants", "member")


def _keyed(value, key_of: dict, member_args: tuple) -> Any:
    if isinstance(value, dict):
        return {k: (_keyed(v, key_of, member_args) if k in member_args or isinstance(v, (dict, list)) else v)
                for k, v in value.items()} if any(k in member_args for k in value) or any(
                    isinstance(v, (dict, list)) for v in value.values()) else dict(value)
    if isinstance(value, list):
        return [_keyed(v, key_of, member_args) for v in value]
    return key_of.get(value, value)


def capture_case(ctx: TurnContext, *, money_tools: set[str], members: list[dict], day: str,
                 member_args: tuple = DEFAULT_MEMBER_ARGS) -> dict | None:
    """A real turn as a ``review: true`` eval case (design §5.5; review F9).

    Only the money tools' calls become the expectation — scaffolding (``find_members``,
    ``resolve_date``) is the model's business and must not become mandatory. Member
    ids in those calls and the sender become keys (``m{id}``) against a ``members``
    snapshot (display name and handle only), so the case is replayable once a human
    adds a world. Returns ``None`` when the turn called no money tool.
    """
    result = ctx.result
    # the manager's own money calls; a sub-agent's are its answer, not this turn's expectation (design §6)
    calls = [inv for inv in (getattr(result, "tools", None) or [])
             if inv.name in money_tools and getattr(inv, "from_agent", None) is None]
    if not calls:
        return None
    key_of = {m["id"]: f"m{m['id']}" for m in members}
    principal_id = ctx.principal.id if ctx.principal is not None else None
    if principal_id is not None and principal_id not in key_of:
        key_of[principal_id] = f"m{principal_id}"
        members = [*members, {"id": principal_id, "name": ctx.principal.name, "handle": None}]
    tools, args = [], {}
    for inv in calls:
        if inv.name not in tools:
            tools.append(inv.name)
        args[inv.name] = _keyed(dict(inv.args or {}), key_of, member_args)     # the last call wins
    turn_id = ctx.turn_id or getattr(result, "turn_id", None) or "turn"
    return {
        "id": f"cap-{ctx.space_id}-{turn_id}", "source": "captured", "day": day,
        "actor": key_of.get(principal_id, str(principal_id)),
        "members": [{"key": key_of[m["id"]], "display_name": m.get("name"), "nickname": m.get("handle")} for m in members],
        "prior_steps": [], "message": ctx.text, "history": ctx.history or "", "images": [],
        "had_images": bool(ctx.images), "expect": {"tools": tools, "args": args},
        "tags": ["captured"], "review": True,
    }


class EvalCapture(BasePlugin):
    """``kernos.after.eval_capture``: write this turn as a candidate eval case. Opt-in
    per profile; ``sample`` thins it, ``only_tool_turns`` keeps prose-only turns out,
    ``keep_days`` bounds how long unreviewed captures stay."""

    id, version, stage = "kernos.after.eval_capture", "1", Stage.after
    config_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"sample": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0},
                       "only_tool_turns": {"type": "boolean", "default": True},
                       "keep_days": {"type": "integer", "minimum": 1, "default": 90}},
    }

    def __init__(self, sink, packs, adapters, *, rng=None) -> None:
        """``sink(space_id, case, keep_days)`` stores the case (the host knows the
        business a space belongs to); ``packs`` names the money tools; ``adapters``
        supplies ``principals`` and ``clock``."""
        self._sink, self._packs, self._a = sink, packs, adapters
        self._rng = rng or __import__("random").random

    async def run(self, ctx: TurnContext, config: dict) -> None:
        if ctx.result is None or self._rng() > float(config.get("sample", 1.0)):
            return
        tool_packs = ctx.profile.tool_packs if ctx.profile is not None else []
        money: set[str] = set()
        for pack, _ in self._packs.enabled(tool_packs):
            money |= set(getattr(pack, "money_tools", ()) or ())
        if not money and config.get("only_tool_turns", True):
            return
        members = self._a.principals.list(ctx.space_id) if getattr(self._a, "principals", None) else []
        case = capture_case(ctx, money_tools=money, members=members, day=self._a.clock.today().isoformat())
        if case is None:
            return
        self._sink(ctx.space_id, case, int(config.get("keep_days", 90)))
        ctx.extras["captured_case"] = case["id"]
