"""The ``model`` stage. In Phase 1 a passthrough: the sidecar routes text vs vision
itself (``session.js:resolveModel``), and duplicating that here would cross the Pi
design's boundary (review finding 14). The stage exists so a profile can later
choose per-turn routing in Python if it ever wants to."""
from __future__ import annotations

from kernos.kernel.context import Stage, TurnContext
from kernos.kernel.plugin import BasePlugin


class ModelPassthrough(BasePlugin):
    id, version, stage = "kernos.model.passthrough", "1", Stage.model
    config_schema = {"type": "object", "additionalProperties": False, "properties": {}}

    async def run(self, ctx: TurnContext, config: dict) -> None:
        profile = ctx.profile
        if profile is not None:
            ctx.model = profile.models.text
            ctx.vision_model = profile.models.vision
            ctx.thinking = profile.models.thinking
            ctx.caps = {"max_tools": profile.caps.max_tools, "max_seconds": profile.caps.max_seconds}
