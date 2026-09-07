from app.prompt import build_system_prompt
from kernos.kernel import BasePlugin, Stage, TurnContext


class PhoenixSystemPrompt(BasePlugin):
    """The system prompt is code in Phase 1 (review finding 8): ``build_system_prompt``
    has conditionals (`who` only with a sender, `member_id` only when known) that a
    plain ``{{var}}`` template cannot express. Phase 2 decides the template syntax."""

    id, version, stage = "app.prompt.phoenix", "1", Stage.prompt
    config_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    async def run(self, ctx: TurnContext, config: dict) -> None:
        ctx.system = build_system_prompt(sender_name=ctx.principal.name, sender_id=ctx.principal.id)
