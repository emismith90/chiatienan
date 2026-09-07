"""``kernos.render.packs``: the outcome of a turn, decided by the enabled packs."""
from __future__ import annotations

from typing import Any

from kernos.kernel.context import Body, Draft, Stage, TurnContext
from kernos.kernel.plugin import BasePlugin
from kernos.packs import PackRegistry

#: Today's three empty-turn bodies (from `chat._empty_turn_body`), as defaults a
#: profile may override — they are host text, so the kernel only ships defaults.
DEFAULT_EMPTY = {
    "error": "⚠️ {error}",
    "capped": ("⏱️ That took too long and I ran out of time before answering — "
               "nothing was recorded. Ask me again."),
    "none": "⚠️ I came back with nothing — nothing was recorded. Ask me again.",
}


def empty_turn_body(result: Any, texts: dict | None = None) -> str:
    """What to say when the model produced no text. Three different things end up
    here and they must not read the same: an ``error`` (the run broke), a ``capped``
    turn (cut before it wrote anything — deliberately not an error), and an empty
    completion. All three say "nothing was recorded"."""
    t = {**DEFAULT_EMPTY, **(texts or {})}
    if getattr(result, "error", None):
        return t["error"].format(error=result.error)
    if getattr(result, "capped", False):
        return t["capped"]
    return t["none"]


class PackRender(BasePlugin):
    id, version, stage = "kernos.render.packs", "1", Stage.render
    config_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"empty": {"type": "object", "additionalProperties": False,
                                 "properties": {k: {"type": "string"} for k in DEFAULT_EMPTY}}},
    }

    def __init__(self, packs: PackRegistry) -> None:
        self._packs = packs

    async def run(self, ctx: TurnContext, config: dict) -> None:
        result = ctx.result
        tool_packs = ctx.profile.tool_packs if ctx.profile is not None else []
        for pack, _overrides in self._packs.enabled(tool_packs):
            outcome = pack.render(result)
            if outcome is None:
                continue
            if isinstance(outcome, Draft):
                stamps = pack.draft_kinds()[outcome.kind].stamps
                payload = dict(outcome.payload)
                if "raw_input" in stamps:
                    payload["raw_input"] = ctx.text
                if "logged_by" in stamps:
                    payload["logged_by"] = str(ctx.principal.id)
                if "turn_id" in stamps:
                    payload["turn_id"] = result.turn_id
                outcome = Draft(outcome.kind, payload)
            ctx.outcome = outcome
            return
        text = result.final_text if result is not None else ""
        ctx.outcome = Body(text or empty_turn_body(result, config.get("empty")), None,
                           claimed_by_pack=not bool(text))
