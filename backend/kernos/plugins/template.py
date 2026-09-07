"""``kernos.prompt.template``: the system prompt from content (plan Task 2.7)."""
from __future__ import annotations

from kernos.adapters.protocols import HostAdapters
from kernos.kernel.context import Stage, TurnContext
from kernos.kernel.plugin import BasePlugin
from kernos.template import ALLOWED_VARS, render


def prompt_variables(ctx: TurnContext, adapters: HostAdapters | None) -> dict:
    persona = ctx.profile.persona if ctx.profile is not None else None
    today = adapters.clock.today().isoformat() if adapters is not None else None
    return {
        "persona": {
            "handle": persona.handle if persona else None,
            "name": persona.name if persona else None,
            "aliases": list(persona.aliases) if persona else [],
            "language": persona.language if persona else None,
        },
        "sender": {"name": ctx.principal.name, "member_id": ctx.principal.id},
        "today": today,
        "space": {"id": ctx.space_id},
    }


class TemplatePrompt(BasePlugin):
    id, version, stage = "kernos.prompt.template", "1", Stage.prompt
    config_schema = {
        "type": "object", "additionalProperties": False, "properties": {},
        "description": "Renders profile.prompt.body then profile.prompt.append with the closed "
                       "variable set: " + ", ".join(sorted(ALLOWED_VARS)),
    }

    def __init__(self, adapters: HostAdapters | None = None) -> None:
        self._a = adapters

    async def run(self, ctx: TurnContext, config: dict) -> None:
        prompt = ctx.profile.prompt
        variables = prompt_variables(ctx, self._a)
        parts = [render(prompt.body, variables)]
        parts.extend(render(section, variables) for section in prompt.append)
        ctx.system = "\n\n".join(p for p in parts if p)
