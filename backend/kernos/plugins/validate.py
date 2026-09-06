"""Pre-built tool-call validators (design §5.4; plan Task 6.2).

A ``ValidationRule`` in a profile is one of these plugins plus config, guarding one
tool: ``sum_equals`` (Σ over ``left`` = Σ over ``right``, within ``tolerance``),
``non_negative`` (every value at ``paths`` ≥ 0), ``unique_members`` (no member id
twice at ``path``). Each exists twice — at ``validate_args`` (the model's arguments,
before the tool runs) and at ``validate_result`` (what the tool returned) — because a
plugin has one stage. Failing with ``on_fail: return_error`` refuses the call: the tool
answers ``{ok: false, error}`` and the model asks the user; ``warn`` records a verdict
and lets the call through. All three ``handles_money``: they exist to keep money
honest, so gate 2 counts them.

Paths are a tiny subset: ``field``, ``field.sub``, ``field[*].sub`` — a top-level
value, a nested value, or one field of every item in a list. A missing path reads as
0; a non-integer value fails the rule closed (the reason names it).
"""
from __future__ import annotations

import re
from typing import Any

from kernos.kernel.context import Stage, TurnContext, Verdict
from kernos.kernel.plugin import BasePlugin

PATH_RE = r"^[a-z_][a-z0-9_]*(\[\*\]\.[a-z_][a-z0-9_]*|\.[a-z_][a-z0-9_]*)?$"
_PATH = re.compile(PATH_RE)


class PathError(ValueError):
    pass


def values_at(obj: Any, path: str) -> list[Any]:
    """The values a path names in ``obj`` (``[]`` when the path is absent)."""
    if not _PATH.match(path or ""):
        raise PathError(f"bad path {path!r}")
    if "[*]." in path:
        head, sub = path.split("[*].", 1)
        items = (obj or {}).get(head) if isinstance(obj, dict) else None
        if not isinstance(items, list):
            return []
        return [i.get(sub) for i in items if isinstance(i, dict) and sub in i]
    if "." in path:
        head, sub = path.split(".", 1)
        inner = (obj or {}).get(head) if isinstance(obj, dict) else None
        return [inner[sub]] if isinstance(inner, dict) and sub in inner else []
    return [obj[path]] if isinstance(obj, dict) and path in obj else []


def _int_sum(obj: Any, paths: list[str]) -> tuple[int, str | None]:
    """Σ of integer values at ``paths``; a non-integer is a problem string instead."""
    total = 0
    for path in paths:
        for v in values_at(obj, path):
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, int):
                return 0, f"{path} has a non-integer value {v!r}"
            total += v
    return total, None


def _subject(ctx: TurnContext, stage: Stage) -> Any:
    call = ctx.extras.get("tool_call") or {}
    return call.get("args") if stage is Stage.validate_args else call.get("result")


def _fail(config: dict, reason: str) -> Verdict:
    severity = "warn" if config.get("on_fail") == "warn" else "block"
    return Verdict(False, severity, f"{config.get('rule') or 'rule'}: {reason}")


_COMMON = {
    "rule": {"type": "string"},
    "tool": {"type": ["string", "null"]},
    "on_fail": {"type": "string", "enum": ["return_error", "warn"]},
}


class _PerCall(BasePlugin):
    handles_money = True

    def __init__(self, stage: Stage = Stage.validate_args) -> None:
        self.stage = Stage(stage)
        if self.stage is Stage.validate_result:
            self.id = f"{self.base_id}.result"
        else:
            self.id = self.base_id


class SumEquals(_PerCall):
    """Σ ``left`` = Σ ``right`` within ``tolerance`` (default 0). Poker's chips-conserved:
    ``left: entries[*].buy_in``, ``right: [entries[*].cash_out, house]``."""

    base_id = "kernos.validate.sum_equals"
    config_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["left", "right"],
        "properties": {**_COMMON, "left": {"type": "string", "pattern": PATH_RE},
                       "right": {"type": "array", "items": {"type": "string", "pattern": PATH_RE}, "minItems": 1},
                       "tolerance": {"type": "integer", "minimum": 0, "default": 0}},
    }

    async def run(self, ctx: TurnContext, config: dict) -> Verdict | None:
        subject = _subject(ctx, self.stage)
        left, problem = _int_sum(subject, [config["left"]])
        if problem:
            return _fail(config, problem)
        right, problem = _int_sum(subject, list(config["right"]))
        if problem:
            return _fail(config, problem)
        delta = left - right
        if abs(delta) > int(config.get("tolerance") or 0):
            return _fail(config, f"Σ {config['left']} = {left:,} but Σ {' + '.join(config['right'])} = {right:,} "
                                 f"(delta {delta:+,}) — the numbers do not add up; ask the user which one is off")
        return None


class NonNegative(_PerCall):
    base_id = "kernos.validate.non_negative"
    config_schema = {
        "type": "object", "additionalProperties": False, "required": ["paths"],
        "properties": {**_COMMON, "paths": {"type": "array", "items": {"type": "string", "pattern": PATH_RE}, "minItems": 1}},
    }

    async def run(self, ctx: TurnContext, config: dict) -> Verdict | None:
        subject = _subject(ctx, self.stage)
        for path in config["paths"]:
            for v in values_at(subject, path):
                if v is None:
                    continue
                if isinstance(v, bool) or not isinstance(v, int):
                    return _fail(config, f"{path} has a non-integer value {v!r}")
                if v < 0:
                    return _fail(config, f"{path} must not be negative (got {v:,})")
        return None


class UniqueMembers(_PerCall):
    """No member twice at ``path`` — a list of ids, or of objects with ``member``."""

    base_id = "kernos.validate.unique_members"
    config_schema = {
        "type": "object", "additionalProperties": False, "required": ["path"],
        "properties": {**_COMMON, "path": {"type": "string", "pattern": PATH_RE}},
    }

    async def run(self, ctx: TurnContext, config: dict) -> Verdict | None:
        subject = _subject(ctx, self.stage)
        seen: set = set()
        for v in values_at(subject, config["path"]):
            items = v if isinstance(v, list) else [v]
            for item in items:
                member = item.get("member") if isinstance(item, dict) else item
                if member in seen:
                    return _fail(config, f"member {member!r} appears more than once at {config['path']}")
                seen.add(member)
        return None


def validators() -> list[BasePlugin]:
    """Every validator, at both per-call stages — what a kernel registers."""
    return [cls(stage) for cls in (SumEquals, NonNegative, UniqueMembers)
            for stage in (Stage.validate_args, Stage.validate_result)]
