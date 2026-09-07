"""The runner: stages in order, plugins in order, a trace of everything.

Deliberately small (design §0.1 warns that a kernel that grows features is a
framework wearing a costume). It sequences, it records, it stops on ``block``,
and it re-raises — the host decides what an exception means for its user.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from kernos.kernel.context import PIPELINE_ORDER, SINGLE_OWNER, Stage, TurnContext, Verdict
from kernos.kernel.plugin import Plugin, key


log = logging.getLogger("kernos.kernel")


class PipelineError(ValueError):
    """A pipeline that cannot be run: a single-owner stage with 0 or 2+ plugins."""


class Pipeline:
    def __init__(self, stages: dict[Stage, list[tuple[Plugin, dict]]]):
        self._stages: dict[Stage, list[tuple[Plugin, dict]]] = {
            Stage(stage): list(entries) for stage, entries in stages.items()
        }
        problems = []
        for stage in SINGLE_OWNER:
            n = len(self._stages.get(stage, []))
            if n != 1:
                problems.append(f"stage '{stage}' needs exactly one plugin, has {n}")
        for stage, entries in self._stages.items():
            for plugin, _config in entries:
                if Stage(plugin.stage) != stage:
                    problems.append(f"{key(plugin)} is a '{plugin.stage}' plugin, listed under '{stage}'")
        if problems:
            raise PipelineError("; ".join(problems))

    def plugins(self, stage: Stage) -> list[tuple[Plugin, dict]]:
        return list(self._stages.get(stage, []))

    def describe(self) -> list[dict]:
        return [
            {"stage": str(stage), "plugin": plugin.id, "version": plugin.version, "config": config}
            for stage in (*PIPELINE_ORDER, Stage.validate_args, Stage.validate_result)
            for plugin, config in self._stages.get(stage, [])
        ]

    async def run(self, ctx: TurnContext, *, through: Stage | str | None = None) -> TurnContext:
        """Stages in order. ``after`` always runs — in a ``finally`` — so a turn that
        raised is still observed (plan Task 4.1); its plugins are guarded: one that
        raises is recorded and logged, never re-raised, because observability must
        not break the turn. A ``BaseException`` (cancellation) still propagates.

        ``through`` runs the stages up to and including that one (Phase 7 review F5):
        a sub-agent's nested run stops at ``validate`` — it posts nothing and is not
        observed as a turn of its own — and ``after`` runs only when it is included.
        ``ctx.extras["started_at"]`` (monotonic seconds) is stamped so the run stage's
        tools can tell how much of the turn's time budget is left."""
        ctx.extras["pipeline"] = self          # the run stage's tool executor validates calls through it
        ctx.extras["started_at"] = time.monotonic()
        stages = PIPELINE_ORDER
        if through is not None:
            stages = PIPELINE_ORDER[:PIPELINE_ORDER.index(Stage(through)) + 1]
        try:
            for stage in stages:
                if stage is Stage.after:
                    continue
                await self._run_stage(stage, ctx, self._stages.get(stage, []))
        finally:
            if Stage.after in stages:
                await self._run_after(ctx)
        return ctx

    async def _run_after(self, ctx: TurnContext) -> None:
        for plugin, config in self._stages.get(Stage.after, []):
            started = time.perf_counter()
            try:
                await plugin.run(ctx, config)
            except Exception as exc:  # noqa: BLE001 — an after plugin must not break the turn
                log.warning("after plugin %s failed: %s: %s", plugin.id, type(exc).__name__, exc)
                ctx.record(Stage.after, plugin.id, plugin.version,
                           (time.perf_counter() - started) * 1000, "error",
                           error=f"{type(exc).__name__}: {exc}")
            else:
                ctx.record(Stage.after, plugin.id, plugin.version, (time.perf_counter() - started) * 1000, "ok")

    async def validate(self, stage: Stage, ctx: TurnContext, *, name: str, args: dict | None,
                       result: Any = None) -> Verdict | None:
        """Run the validators of ``validate_args`` / ``validate_result`` for one tool call
        (plan Task 6.2). Only the rules whose config names this ``tool`` (or none) run;
        the call is on ``ctx.extras["tool_call"]``. Returns the first refusing verdict
        (``block``), else ``None``; ``warn`` verdicts are recorded and let the call
        through. Never touches ``ctx.stopped`` or ``ctx.outcome`` — a refused tool call is
        not a stopped turn. Meant for the `run` stage's tool executor.
        """
        if stage not in (Stage.validate_args, Stage.validate_result):
            raise PipelineError(f"validate() is for per-call stages, not '{stage}'")
        ctx.extras["tool_call"] = {"name": name, "args": args or {}, "result": result}
        refused: Verdict | None = None
        for plugin, config in self._stages.get(stage, []):
            tool = config.get("tool")
            if tool is not None and tool != name:
                continue
            started = time.perf_counter()
            try:
                out = await plugin.run(ctx, config)
            except Exception as exc:  # noqa: BLE001 — a broken rule refuses the call, never crashes the turn
                ms = (time.perf_counter() - started) * 1000
                ctx.record(stage, plugin.id, plugin.version, ms, "error", tool=name, rule=config.get("rule"),
                           error=f"{type(exc).__name__}: {exc}")
                refused = refused or Verdict(False, "block", f"{config.get('rule') or plugin.id}: validator failed ({type(exc).__name__}: {exc})")
                continue
            ms = (time.perf_counter() - started) * 1000
            if isinstance(out, Verdict) and not out.ok:
                ctx.record(stage, plugin.id, plugin.version, ms, out.severity, tool=name, rule=config.get("rule"), reason=out.reason)
                if out.severity == "block" and refused is None:
                    refused = out
            else:
                ctx.record(stage, plugin.id, plugin.version, ms, "ok", tool=name, rule=config.get("rule"))
        return refused

    async def _run_stage(self, stage: Stage, ctx: TurnContext,
                         entries: Iterable[tuple[Plugin, dict]]) -> Verdict | None:
        blocked: Verdict | None = None
        for plugin, config in entries:
            if blocked is not None:
                ctx.record(stage, plugin.id, plugin.version, 0.0, "skipped")
                continue
            started = time.perf_counter()
            try:
                out = await plugin.run(ctx, config)
            except Exception as exc:  # noqa: BLE001 — recorded, then the host decides
                ctx.record(stage, plugin.id, plugin.version,
                           (time.perf_counter() - started) * 1000, "error",
                           error=f"{type(exc).__name__}: {exc}")
                raise
            ms = (time.perf_counter() - started) * 1000
            if isinstance(out, Verdict) and not out.ok:
                ctx.record(stage, plugin.id, plugin.version, ms, out.severity, reason=out.reason)
                if out.severity == "block":
                    blocked = out
                    if out.replacement is not None:
                        ctx.outcome = out.replacement
                    ctx.stopped = True
            else:
                ctx.record(stage, plugin.id, plugin.version, ms, "ok")
        return blocked
