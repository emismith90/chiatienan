import app.agent as agent_mod
from kernos.kernel import BasePlugin, Stage, TurnContext


class LegacyRunTurn(BasePlugin):
    """The ``run`` stage through the frozen ``agent.run_turn``.

    The resolved profile reaches ``run_turn`` on ``ToolContext.engine_spec`` and the
    rendered prompt on ``system_override``/``message_override`` (plan Task 1.4). The
    function itself is looked up on ``app.agent`` **at call time**, so the 18 test
    fakes that patch it keep intercepting production turns.
    """

    id, version, stage = "app.run.legacy", "1", Stage.run
    config_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    async def run(self, ctx: TurnContext, config: dict) -> None:
        tool_ctx = ctx.tool_ctx
        agent = ctx.extras.get("agent")
        if ctx.profile is not None:
            tool_ctx.engine_spec = ctx.profile.to_engine_spec()
            packs = [t.model_dump() for t in ctx.profile.tool_packs]
            if agent and agent.get("delegates_to"):
                packs.append({"pack": "delegation", "tools": {}})      # `ask_<sub>` tools (design §6)
            tool_ctx.tool_config = {"packs": packs} if packs else None
        tool_ctx.system_override = ctx.system
        tool_ctx.message_override = ctx.message
        # who is running, how deep, and the root agent's depth limit (Phase 7 review F8)
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
        emit = ctx.sink.emit_raw if ctx.sink is not None else None
        result = await agent_mod.run_turn(
            ctx.text, tool_ctx, images=ctx.images or None, emit=emit,
            memory=ctx.memory or None, history=ctx.history or None)
        ctx.result = result
        ctx.turn_id = result.turn_id
