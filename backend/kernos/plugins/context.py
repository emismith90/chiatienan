"""Host-agnostic ``context`` plugins: rollover, memory, history, image carry-over.

Each is a move of a step ``chat.run_bot_turn`` used to do inline (plan Task 1.6),
generalised over the host adapters. Rollover runs **first** by profile order —
the turn that ages messages out must see the new summary and a history window
starting at the advanced watermark (review finding 2).
"""
from __future__ import annotations

from datetime import timedelta

from kernos.adapters.protocols import HostAdapters
from kernos.kernel.context import Stage, TurnContext
from kernos.kernel.plugin import BasePlugin

_EMPTY = {"type": "object", "additionalProperties": False, "properties": {}}


async def rollover_once(space_id: str, *, adapters: HostAdapters, window_weeks: int,
                        header: str, kind: str = "rollover") -> bool:
    """Fold messages older than the window into long-term memory and advance the
    watermark. Returns whether a summary was written.

    On a blank/failed summary the watermark is left untouched so the aged messages
    are retried next turn — never silently dropped.
    """
    if adapters.completion is None:
        return False
    cutoff = adapters.clock.now() - timedelta(weeks=window_weeks)
    watermark = adapters.memory.watermark(space_id)
    rendered, through_id = adapters.history.aged(space_id, watermark=watermark, older_than=cutoff)
    if through_id is None:
        return False
    summary = await adapters.completion.complete(rendered, kind=kind)
    if not summary:
        return False
    adapters.memory.append_summary(space_id, summary_text=summary, through_id=through_id,
                                   through_at=adapters.clock.now().isoformat(), header=header)
    return True


class Rollover(BasePlugin):
    id, version, stage = "kernos.context.rollover", "1", Stage.context
    config_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "window_weeks": {"type": "integer", "minimum": 1},
            "header": {"type": "string"},
            "kind": {"type": "string"},
        },
        "required": ["window_weeks"],
    }

    def __init__(self, adapters: HostAdapters) -> None:
        self._a = adapters

    async def run(self, ctx: TurnContext, config: dict) -> None:
        if ctx.depth > 0:
            return None      # a sub-agent's nested run must not fold the room's history mid-turn (Phase 7 review F6)
        await rollover_once(ctx.space_id, adapters=self._a, window_weeks=config["window_weeks"],
                            header=config.get("header", "Auto-saved"), kind=config.get("kind", "rollover"))


class MemoryLoad(BasePlugin):
    id, version, stage = "kernos.context.memory", "1", Stage.context
    config_schema = _EMPTY

    def __init__(self, adapters: HostAdapters) -> None:
        self._a = adapters

    async def run(self, ctx: TurnContext, config: dict) -> None:
        ctx.memory = self._a.memory.load(ctx.space_id) or None


class RecentHistory(BasePlugin):
    id, version, stage = "kernos.context.history", "1", Stage.context
    config_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"max_messages": {"type": "integer", "minimum": 1},
                       "bot_label": {"type": "string"}},
        "required": ["max_messages"],
    }

    def __init__(self, adapters: HostAdapters) -> None:
        self._a = adapters

    async def run(self, ctx: TurnContext, config: dict) -> None:
        ctx.history = self._a.history.render(
            ctx.space_id, bot_label=config.get("bot_label", "assistant"),
            since_id=self._a.memory.watermark(ctx.space_id),
            limit=config["max_messages"], before_id=ctx.before_id) or None


class ImageLookback(BasePlugin):
    """"Bill pasted, then @bot in the next message" is the normal way people use
    this — carry the recent bill into the turn when the message itself has none."""

    id, version, stage = "kernos.context.images", "1", Stage.context
    config_schema = _EMPTY

    def __init__(self, adapters: HostAdapters) -> None:
        self._a = adapters

    async def run(self, ctx: TurnContext, config: dict) -> None:
        if not ctx.images:
            ctx.images = list(self._a.history.recent_images(ctx.space_id, before_id=ctx.before_id) or [])
