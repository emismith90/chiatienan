import app.agent as agent_mod
from kernos.kernel import BasePlugin, Stage, TurnContext
from kernos.plugins.run import prepare_tool_context


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
        tool_ctx = prepare_tool_context(ctx)           # packs, agent, depth, per-call validators (shared with kernos.run.engine)
        if ctx.profile is not None:
            tool_ctx.engine_spec = ctx.profile.to_engine_spec()
        tool_ctx.system_override = ctx.system
        tool_ctx.message_override = ctx.message
        emit = ctx.sink.emit_raw if ctx.sink is not None else None
        result = await agent_mod.run_turn(
            ctx.text, tool_ctx, images=ctx.images or None, emit=emit,
            memory=ctx.memory or None, history=ctx.history or None)
        ctx.result = result
        ctx.turn_id = result.turn_id
