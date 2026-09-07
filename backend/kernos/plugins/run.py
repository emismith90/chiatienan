"""``kernos.run.engine``: the ``run`` stage over an :class:`~kernos.engine.base.Engine`
(Phase 9 review F1) — what a host uses when it has no legacy turn function of its own.

Composes the enabled packs' tools for this turn, hands the engine the profile's
``EngineSpec`` (system prompt from the prompt stage, caps clamped by a nested run's
budget), executes tool calls with the host-side policy the lunch host proved —
an unknown tool or a raising tool is ``{ok: false, error}``, never a dead turn; the
profile's per-call validation rules are consulted before and after; an awaitable
result (a delegation) is awaited; a sub-agent's invocations merge in order — and
records the ``TurnResult`` on the context.
"""
from __future__ import annotations

import inspect
import logging
import time
import uuid
from dataclasses import replace
from typing import Any, Callable

from kernos.engine.base import Engine, merge_sub_invocations
from kernos.kernel.context import Stage, TurnContext
from kernos.kernel.plugin import BasePlugin
from kernos.packs import PackRegistry, compose_tools

log = logging.getLogger("kernos.run")


def prepare_tool_context(ctx: TurnContext) -> Any:
    """Put on the host's tool context what packs read during the run stage: the profile's
    packs (plus ``delegation`` when the agent delegates), the agent, depth, the root's
    ``max_depth``, the turn and its start time. Shared with the lunch host's run plugin."""
    tool_ctx = ctx.tool_ctx
    agent = ctx.extras.get("agent")
    if ctx.profile is not None:
        packs = [t.model_dump() for t in ctx.profile.tool_packs]
        if agent and agent.get("delegates_to"):
            packs.append({"pack": "delegation", "tools": {}})      # `ask_<sub>` tools (design §6)
        tool_ctx.tool_config = {"packs": packs} if packs else None
    tool_ctx.agent = agent
    tool_ctx.depth = ctx.depth
    tool_ctx.max_depth = ctx.extras.get("max_depth") or (agent or {}).get("max_depth")
    tool_ctx.turn = ctx
    tool_ctx.started_at = ctx.extras.get("started_at")
    pipeline = ctx.extras.get("pipeline")
    if pipeline is not None:                       # the profile's tool-scope validation rules (plan Task 6.2)
        async def validate_call(name, args):
            verdict = await pipeline.validate(Stage.validate_args, ctx, name=name, args=args)
            return {"ok": False, "error": verdict.reason} if verdict is not None else None

        async def validate_result(name, args, result):
            verdict = await pipeline.validate(Stage.validate_result, ctx, name=name, args=args, result=result)
            return {"ok": False, "error": verdict.reason} if verdict is not None else None

        tool_ctx.validate_call, tool_ctx.validate_result = validate_call, validate_result
    return tool_ctx


def tool_executor(tool_ctx: Any, tools: dict[str, Any]) -> Callable:
    """The host-side executor policy over ``{name: tool-with-.execute}``."""

    async def call_tool(name: str, args: dict):
        tool_ctx.calls_made += 1
        tool = tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool {name}"}
        before = getattr(tool_ctx, "validate_call", None)
        if before is not None:
            refused = await before(name, args)
            if refused is not None:
                return refused
        try:
            result = tool.execute(args)
            if inspect.isawaitable(result):            # a delegation's nested run
                result = await result
        except Exception as exc:  # noqa: BLE001 — a tool must not kill the turn
            log.exception("[run] tool %s raised", name)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        after = getattr(tool_ctx, "validate_result", None)
        if after is not None:
            refused = await after(name, args, result)
            if refused is not None:
                return refused
        return result

    return call_tool


class EngineRun(BasePlugin):
    id, version, stage = "kernos.run.engine", "1", Stage.run
    config_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    def __init__(self, engine: Engine | Callable[[], Engine], packs: PackRegistry) -> None:
        """``engine`` is an :class:`Engine` or a zero-argument factory (looked up per turn,
        so a test can swap it)."""
        self._engine, self._packs = engine, packs

    async def run(self, ctx: TurnContext, config: dict) -> None:
        if ctx.profile is None:
            raise ValueError("kernos.run.engine needs a resolved profile on the context")
        tool_ctx = prepare_tool_context(ctx)
        turn_id = uuid.uuid4().hex[:12]
        tool_ctx.turn_id = turn_id
        if getattr(tool_ctx, "started_at", None) is None:
            tool_ctx.started_at = time.monotonic()
        spec = ctx.profile.to_engine_spec(system=ctx.system or "")
        spec = replace(spec, **(getattr(tool_ctx, "caps_override", None) or {}))
        tools = compose_tools(self._packs, (tool_ctx.tool_config or {}).get("packs", []), tool_ctx)
        engine = self._engine() if callable(self._engine) and not hasattr(self._engine, "run") else self._engine
        message = ctx.message if ctx.message is not None else ctx.text
        result = await engine.run(
            spec, turn_id=turn_id, message=message, images=list(ctx.images or []),
            tools=[t.manifest() for t in tools.values()], call_tool=tool_executor(tool_ctx, tools),
            emit=ctx.sink.emit_raw if ctx.sink is not None else None)
        if getattr(tool_ctx, "sub_invocations", None):
            result.tools = merge_sub_invocations(result.tools, tool_ctx.sub_invocations)
        ctx.result = result
        ctx.turn_id = result.turn_id or turn_id
