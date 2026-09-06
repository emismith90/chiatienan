"""The runner: stages in order, plugins in order, a trace of everything.

Deliberately small (design §0.1 warns that a kernel that grows features is a
framework wearing a costume). It sequences, it records, it stops on ``block``,
and it re-raises — the host decides what an exception means for its user.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

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

    async def run(self, ctx: TurnContext) -> TurnContext:
        """Stages in order. ``after`` always runs — in a ``finally`` — so a turn that
        raised is still observed (plan Task 4.1); its plugins are guarded: one that
        raises is recorded and logged, never re-raised, because observability must
        not break the turn. A ``BaseException`` (cancellation) still propagates."""
        try:
            for stage in PIPELINE_ORDER:
                if stage is Stage.after:
                    continue
                await self._run_stage(stage, ctx, self._stages.get(stage, []))
        finally:
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

    async def validate(self, stage: Stage, ctx: TurnContext) -> Verdict | None:
        """Run the validators of ``validate_args`` / ``validate_result`` for one tool call.

        Returns the first ``block`` verdict, else ``None``. Meant for the `run`
        stage's tool executor; the pipeline itself never calls it.
        """
        if stage not in (Stage.validate_args, Stage.validate_result):
            raise PipelineError(f"validate() is for per-call stages, not '{stage}'")
        return await self._run_stage(stage, ctx, self._stages.get(stage, []))

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
