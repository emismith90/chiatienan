"""Typed turn events (design §12.4) and the mapping to chiatienan's legacy wire format.

The kernel emits ``TurnEvent``s. A host chooses a sink: chiatienan's maps them to
the frozen ``agent.*`` names its UI already consumes; a new host can pick the
AG-UI sink (Phase 9). Live events (text deltas, tool starts) go to
``TurnContext.sink`` during ``run``; ``message.republished`` and anything else
that must wait for the writer lock go to ``TurnContext.pending_events``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

RUN_STARTED = "run.started"
TEXT_DELTA = "text.delta"
TOOL_START = "tool.start"
TOOL_RESULT = "tool.result"
RUN_FINISHED = "run.finished"
RUN_ERROR = "run.error"
SUB_STARTED = "sub.started"
SUB_FINISHED = "sub.finished"
VALIDATION_WARNED = "validation.warned"
VALIDATION_BLOCKED = "validation.blocked"
MESSAGE_REPUBLISHED = "message.republished"

EVENT_TYPES = frozenset({
    RUN_STARTED, TEXT_DELTA, TOOL_START, TOOL_RESULT, RUN_FINISHED, RUN_ERROR,
    SUB_STARTED, SUB_FINISHED, VALIDATION_WARNED, VALIDATION_BLOCKED, MESSAGE_REPUBLISHED,
})


@dataclass
class TurnEvent:
    type: str
    turn_id: str | None = None
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unknown TurnEvent type {self.type!r}")


#: `TurnEvent.type` → the `agent.*` name `frontend/src/hooks/use-room.ts` consumes.
_LEGACY_NAMES = {
    RUN_STARTED: "agent.run.started",
    TEXT_DELTA: "agent.text.delta",
    TOOL_START: "agent.tool.start",
    TOOL_RESULT: "agent.tool.result",
    RUN_FINISHED: "agent.run.finished",
    RUN_ERROR: "agent.run.error",
    SUB_STARTED: "agent.sub.started",
    SUB_FINISHED: "agent.sub.finished",
    VALIDATION_WARNED: "agent.validation.warned",
    VALIDATION_BLOCKED: "agent.validation.blocked",
}


def to_legacy(event: TurnEvent) -> dict:
    """The dict chiatienan's SSE stream publishes for this event.

    A republished card is not an ``agent.*`` event but a full ``message`` payload,
    exactly as ``run_bot_turn`` emits it today (``{"type": "message", **card}``).
    """
    if event.type == MESSAGE_REPUBLISHED:
        return {"type": "message", **event.data}
    out = {"type": _LEGACY_NAMES[event.type]}
    if event.turn_id is not None:
        out["turn_id"] = event.turn_id
    out.update(event.data)
    return out


class LegacyAgentEventSink:
    """An ``EventSink`` over a coroutine that takes the legacy dict.

    ``emit_raw`` passes an already-legacy dict through untouched — the Pi engine
    produces those itself today, and re-typing them would be a translation layer
    the Pi design deleted on purpose (§3.1 of that design).
    """

    def __init__(self, emit: Callable[[dict], Awaitable[None]] | None):
        self._emit = emit

    async def emit(self, event: TurnEvent) -> None:
        if self._emit is not None:
            await self._emit(to_legacy(event))

    async def emit_raw(self, payload: dict) -> None:
        if self._emit is not None:
            await self._emit(payload)


async def flush(ctx_pending: list[Any], sink: Any) -> None:
    """Emit collected events in order, then clear the list. Called by the host
    after it releases its writer lock."""
    for event in list(ctx_pending):
        if isinstance(event, TurnEvent):
            await sink.emit(event)
        else:
            await sink.emit_raw(event)
    ctx_pending.clear()
