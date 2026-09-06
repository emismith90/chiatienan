"""AG-UI events from a turn (design §12.4; Phase 9 review F3).

:class:`AguiEventSink` is an ``EventSink`` a host chooses instead of the legacy one. It is
**stateful**, because AG-UI is a protocol with an order and the kernel's events are not:
the sidecar's ``agent.run.finished`` arrives in the run stage while validation verdicts and
republished cards arrive later, an assistant text message must be closed before a tool
call or the run end, and a dead bridge may report an error before any start. So the sink
synthesises ``RUN_STARTED`` lazily, opens a text message on the first delta and closes it
before anything else, turns a complete tool call into ``TOOL_CALL_START/ARGS/END`` in one
go, and emits ``RUN_FINISHED`` only from :meth:`finish`, which the host calls after it has
flushed its pending events. Everything after ``RUN_ERROR`` is dropped.

Both entry points converge on the legacy dict shape: ``emit(TurnEvent)`` goes through
``to_legacy`` and ``emit_raw`` takes the sidecar's dicts as they are, so one mapping serves
the Pi engine and the kernel's own plugins. The streamed text is the model's live output,
**not** the persisted reply (the sidecar strips pre-tool narration; packs render bodies).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Awaitable, Callable

from kernos.kernel.events import _LEGACY_NAMES, MESSAGE_REPUBLISHED, TurnEvent, to_legacy

_TYPE_FOR_LEGACY = {legacy: typed for typed, legacy in _LEGACY_NAMES.items()}


def from_legacy(payload: dict) -> TurnEvent | None:
    """The inverse of :func:`to_legacy`; ``None`` for a dict that is no turn event."""
    kind = payload.get("type")
    if kind == "message":
        return TurnEvent(MESSAGE_REPUBLISHED, data={k: v for k, v in payload.items() if k != "type"})
    typed = _TYPE_FOR_LEGACY.get(kind)
    if typed is None:
        return None
    data = {k: v for k, v in payload.items() if k not in ("type", "turn_id")}
    return TurnEvent(typed, payload.get("turn_id"), data)


def _now_ms() -> int:
    return int(time.time() * 1000)


class AguiEventSink:
    def __init__(self, write: Callable[[dict], Awaitable[None]], *, thread_id: str,
                 run_id: str | None = None, ids: Callable[[], str] | None = None) -> None:
        self._write, self.thread_id = write, thread_id
        self.run_id = run_id
        self._ids = ids or (lambda: uuid.uuid4().hex[:12])
        self._started = self._finished = False
        self._message_id: str | None = None
        self.events: list[dict] = []          # what was written, for the host's tests

    # ------------------------------------------------------------------ sinks

    async def emit(self, event: TurnEvent) -> None:
        await self.emit_raw(to_legacy(event))

    async def emit_raw(self, payload: dict) -> None:
        if self._finished:
            return
        kind = payload.get("type")
        if self.run_id is None and payload.get("turn_id"):
            self.run_id = payload["turn_id"]
        if kind == "agent.run.started":
            await self._ensure_started()
        elif kind == "agent.text.delta":
            await self._ensure_started()
            if self._message_id is None:
                self._message_id = self._ids()
                await self._out("TEXT_MESSAGE_START", messageId=self._message_id, role="assistant")
            await self._out("TEXT_MESSAGE_CONTENT", messageId=self._message_id, delta=payload.get("delta") or "")
        elif kind == "agent.tool.start":
            await self._ensure_started()
            parent = self._message_id
            await self._close_message()
            call_id = self._call_id(payload)
            await self._out("TOOL_CALL_START", toolCallId=call_id, toolCallName=payload.get("name"),
                            **({"parentMessageId": parent} if parent else {}))
            await self._out("TOOL_CALL_ARGS", toolCallId=call_id, delta=json.dumps(payload.get("args") or {}, ensure_ascii=False))
            await self._out("TOOL_CALL_END", toolCallId=call_id)
        elif kind == "agent.tool.result":
            await self._ensure_started()
            await self._out("TOOL_CALL_RESULT", messageId=self._ids(), toolCallId=self._call_id(payload), role="tool",
                            content=json.dumps(payload.get("result"), ensure_ascii=False, default=str))
        elif kind == "agent.sub.started":
            await self._ensure_started()
            await self._close_message()
            await self._out("STEP_STARTED", stepName=payload.get("agent"))
        elif kind == "agent.sub.finished":
            await self._out("STEP_FINISHED", stepName=payload.get("agent"))
        elif kind == "agent.run.finished":
            await self._close_message()           # RUN_FINISHED waits for finish(): verdicts and cards come later
        elif kind == "agent.run.error":
            await self._ensure_started()
            await self._close_message()
            await self._out("RUN_ERROR", message=payload.get("message") or "run failed")
            self._finished = True
        elif kind in ("agent.validation.warned", "agent.validation.blocked", "message"):
            await self._ensure_started()
            await self._out("CUSTOM", name=kind, value={k: v for k, v in payload.items() if k != "type"})

    async def finish(self) -> None:
        """The run is over and the host has flushed everything: close the message, end the run."""
        if self._finished:
            return
        await self._ensure_started()
        await self._close_message()
        await self._out("RUN_FINISHED")
        self._finished = True

    # ---------------------------------------------------------------- helpers

    def _call_id(self, payload: dict) -> str:
        call_id = str(payload.get("call_id") or self._ids())
        agent = payload.get("agent")
        return f"{agent}/{call_id}" if agent else call_id       # a sub's pi ids collide with the manager's

    async def _ensure_started(self) -> None:
        if not self._started:
            self._started = True
            await self._out("RUN_STARTED")

    async def _close_message(self) -> None:
        if self._message_id is not None:
            await self._out("TEXT_MESSAGE_END", messageId=self._message_id)
            self._message_id = None

    async def _out(self, kind: str, **fields: Any) -> None:
        event = {"type": kind, "timestamp": _now_ms(), **fields}
        if kind in ("RUN_STARTED", "RUN_FINISHED", "RUN_ERROR"):
            event["threadId"], event["runId"] = self.thread_id, self.run_id
        self.events.append(event)
        await self._write(event)
