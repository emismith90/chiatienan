"""Translate a Cursor SDK run stream into AG-UI protocol events.

Replaces agno's ``agno.os.interfaces.agui.router.run_agent`` for the
Cursor-SDK-backed agent. The Atlas frontend speaks AG-UI (TEXT_MESSAGE_* /
TOOL_CALL_* / RUN_*), so this maps the Cursor event stream onto exactly those
event objects — the existing ``/api/copilot/agui`` endpoint encodes them
unchanged.

Design: a pure, stateful :class:`CursorAguiTranslator` (one Cursor message →
a list of AG-UI events) plus a thin async driver :func:`cursor_run_to_agui`.
The translator is sync and side-effect-free so it can be unit-tested with fake
messages; only the driver touches the live (async) run.

Cursor message shapes handled (from ``run.messages()``):
  - ``assistant``  → ``msg.message.content`` text blocks (streamed deltas)
  - ``thinking``   → dropped (kept out of the visible answer; UI shows TEXT/TOOL only)
  - ``tool_call``  → ``call_id, name, status('running'|'completed'|'error'), args, result``
  - ``status``     → lifecycle; ``ERROR`` becomes a RUN_ERROR

Cursor surfaces custom tools and MCP tools under a wrapper tool (``name='mcp'``)
whose args carry ``{providerIdentifier, toolName, args}`` and whose result is an
MCP envelope ``{status, value:{content:[{text:{text}}]}}``. We unwrap both so the
timeline shows the real tool name and a clean result payload.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Iterable

from ag_ui.core import (
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex


def _unwrap_tool_name(name: str | None, args: Any) -> str:
    """Cursor wraps custom/MCP tools as name='mcp' with args.toolName."""
    if name == "mcp" and isinstance(args, dict) and args.get("toolName"):
        return str(args["toolName"])
    return name or "tool"


def _unwrap_tool_args(args: Any) -> dict | Any:
    """Return the inner tool args when wrapped under the mcp envelope."""
    if isinstance(args, dict) and "args" in args and args.get("toolName"):
        return args["args"]
    return args


def _unwrap_tool_result(result: Any) -> str:
    """Flatten Cursor's MCP result envelope to a plain string for the UI.

    ``{status, value:{content:[{text:{text: "<payload>"}}]}}`` → ``"<payload>"``.
    Falls back to JSON for any other shape.
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        value = result.get("value", result)
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, list):
                texts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, dict) and isinstance(text.get("text"), str):
                            texts.append(text["text"])
                        elif isinstance(text, str):
                            texts.append(text)
                if texts:
                    return "\n".join(texts)
    return json.dumps(result, default=str)


def _assistant_text_deltas(msg: Any) -> list[str]:
    """Pull text-block deltas out of an assistant message event."""
    out: list[str] = []
    message = getattr(msg, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return [content] if content else []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                out.append(text)
    return out


class CursorAguiTranslator:
    """Stateful Cursor-message → AG-UI-event mapper (pure, no I/O)."""

    def __init__(self, thread_id: str, run_id: str, error_hint: str = "") -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self._error_hint = error_hint
        self._text_msg_id: str | None = None          # open assistant text message
        self._tools_started: set[str] = set()         # call_ids we emitted START for
        self._tools_done: set[str] = set()            # call_ids we emitted END/RESULT for
        self._error: str | None = None

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> list[Any]:
        return [RunStartedEvent(type=EventType.RUN_STARTED, thread_id=self.thread_id, run_id=self.run_id)]

    def finish(self) -> list[Any]:
        # Close any tool still "running" first, or the UI spins it forever when
        # the run is cut short (bridge death, cancel, shutdown).
        events = self._close_open_tools()
        events.extend(self._close_text())
        if self._error is not None:
            events.append(RunErrorEvent(type=EventType.RUN_ERROR, message=self._error))
        else:
            events.append(
                RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=self.thread_id, run_id=self.run_id)
            )
        return events

    def set_error(self, message: str) -> None:
        """Mark the run as failed (first error wins); ``finish()`` emits RUN_ERROR."""
        if self._error is None:
            self._error = message or "Agent run interrupted"

    def _close_open_tools(self) -> list[Any]:
        """END + RESULT for every tool that started but never completed."""
        events: list[Any] = []
        for call_id in sorted(self._tools_started - self._tools_done):
            self._tools_done.add(call_id)
            events.append(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=call_id))
            events.append(
                ToolCallResultEvent(
                    type=EventType.TOOL_CALL_RESULT,
                    message_id=_new_id(),
                    tool_call_id=call_id,
                    content="⚠️ interrupted",
                )
            )
        return events

    # -- per-message -------------------------------------------------------- #
    def handle(self, msg: Any) -> list[Any]:
        mtype = getattr(msg, "type", None)
        if mtype == "assistant":
            return self._handle_assistant(msg)
        if mtype == "tool_call":
            return self._handle_tool_call(msg)
        if mtype == "status":
            return self._handle_status(msg)
        # thinking / unknown: not surfaced to the AG-UI text stream
        return []

    # -- handlers ----------------------------------------------------------- #
    def _open_text(self) -> list[Any]:
        if self._text_msg_id is not None:
            return []
        self._text_msg_id = _new_id()
        return [
            TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START, message_id=self._text_msg_id, role="assistant"
            )
        ]

    def _close_text(self) -> list[Any]:
        if self._text_msg_id is None:
            return []
        mid = self._text_msg_id
        self._text_msg_id = None
        return [TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=mid)]

    def notice(self, text: str) -> list[Any]:
        """Emit a standalone assistant text note (used for the turn-cap message).

        Appends to the open text bubble if one is streaming, otherwise opens a
        fresh one. ``finish()`` closes it. Pure: returns events, does no I/O.
        """
        events = self._open_text()
        events.append(
            TextMessageContentEvent(
                type=EventType.TEXT_MESSAGE_CONTENT, message_id=self._text_msg_id, delta=text
            )
        )
        return events

    @property
    def tools_completed(self) -> int:
        """Count of tool calls that reached completed/error (for the turn cap)."""
        return len(self._tools_done)

    def _handle_assistant(self, msg: Any) -> list[Any]:
        deltas = _assistant_text_deltas(msg)
        if not deltas:
            return []
        events = self._open_text()
        for delta in deltas:
            events.append(
                TextMessageContentEvent(
                    type=EventType.TEXT_MESSAGE_CONTENT, message_id=self._text_msg_id, delta=delta
                )
            )
        return events

    def _handle_tool_call(self, msg: Any) -> list[Any]:
        call_id = getattr(msg, "call_id", None) or _new_id()
        status = (getattr(msg, "status", "") or "").lower()
        raw_args = getattr(msg, "args", None)
        name = _unwrap_tool_name(getattr(msg, "name", None), raw_args)
        events: list[Any] = []

        if call_id not in self._tools_started:
            # a tool call interrupts any open text message (new UI bubble)
            events.extend(self._close_text())
            self._tools_started.add(call_id)
            events.append(
                ToolCallStartEvent(
                    type=EventType.TOOL_CALL_START, tool_call_id=call_id, tool_call_name=name
                )
            )
            events.append(
                ToolCallArgsEvent(
                    type=EventType.TOOL_CALL_ARGS,
                    tool_call_id=call_id,
                    delta=json.dumps(_unwrap_tool_args(raw_args), default=str),
                )
            )

        if status in ("completed", "error") and call_id not in self._tools_done:
            self._tools_done.add(call_id)
            events.append(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=call_id))
            events.append(
                ToolCallResultEvent(
                    type=EventType.TOOL_CALL_RESULT,
                    message_id=_new_id(),
                    tool_call_id=call_id,
                    content=_unwrap_tool_result(getattr(msg, "result", None)),
                )
            )
        return events

    def _handle_status(self, msg: Any) -> list[Any]:
        status = (getattr(msg, "status", "") or "").upper()
        if status == "ERROR":
            msg_text = getattr(msg, "message", "") or "Cursor agent run failed"
            if not getattr(msg, "message", "") and self._error_hint:
                msg_text = f"{msg_text} — {self._error_hint}"
            self._error = msg_text
        return []


def translate_messages(messages: Iterable[Any], thread_id: str, run_id: str) -> list[Any]:
    """Sync helper: full message list → full AG-UI event list (used in tests)."""
    tr = CursorAguiTranslator(thread_id, run_id)
    events = tr.start()
    for msg in messages:
        events.extend(tr.handle(msg))
    events.extend(tr.finish())
    return events


async def cursor_run_to_agui(
    run: Any,
    thread_id: str,
    run_id: str,
    error_hint: str = "",
    *,
    max_tools: int | None = None,
    max_seconds: int | None = None,
):
    """Async driver: stream AG-UI events from a live Cursor (Async)Run.

    ``run`` must expose an async-iterable ``messages()``. Yields AG-UI event
    objects ready for ``EventEncoder.encode``. ``error_hint`` is appended to the
    RUN_ERROR message when the run fails with no detail (e.g. a model the SDK
    can't run on this plan).

    **Turn-level cap (Component A).** Composer-2.5 can brute-force a single
    question into a multi-minute tool storm (observed: 69 tools / 769s). We bound
    each run here — the one chokepoint that sees *every* tool call (MCP + local,
    via the ``name=='mcp'`` unwrap). When the completed-tool count reaches
    ``max_tools`` OR wall-clock reaches ``max_seconds``, we hard-cancel the run
    (``run.cancel()``), append an honest "stopped to avoid a runaway" note, and
    finish gracefully (RUN_FINISHED, not RUN_ERROR) so the partial answer renders.
    A limit of ``0`` disables it. ``None`` resolves from config/env/defaults via
    :func:`cursor_runner.agent_turn_budget`. The cap is hard, not a soft prompt,
    because composer ignores soft constraints (it would just become a refuse-storm).
    """
    if max_tools is None or max_seconds is None:
        from app.cursor_runner import agent_turn_budget

        cfg_tools, cfg_seconds = agent_turn_budget()
        max_tools = cfg_tools if max_tools is None else max_tools
        max_seconds = cfg_seconds if max_seconds is None else max_seconds

    tr = CursorAguiTranslator(thread_id, run_id, error_hint=error_hint)
    started = time.monotonic()
    for event in tr.start():
        yield event
    try:
        async for msg in run.messages():
            for event in tr.handle(msg):
                yield event

            n_tools = tr.tools_completed
            elapsed = time.monotonic() - started
            over_tools = max_tools and n_tools >= max_tools
            over_time = max_seconds and elapsed >= max_seconds
            if over_tools or over_time:
                limit = "tool-call" if over_tools else "wall-clock"
                logger.warning(
                    "[CursorAgui] turn cap hit (%s) run_id=%s tools=%s elapsed=%.0fs",
                    limit, run_id, n_tools, elapsed,
                )
                if hasattr(run, "supports") and run.supports("cancel"):
                    try:
                        run.cancel()
                    except Exception:  # noqa: BLE001 — cancel is best-effort
                        logger.warning("[CursorAgui] run.cancel() failed", exc_info=True)
                note = (
                    f"\n\n⚠️ I stopped after {n_tools} tool calls (~{elapsed:.0f}s) to avoid a "
                    "runaway. The answer above may be incomplete — try narrowing the question "
                    "(a specific division, person, or date range)."
                )
                for event in tr.notice(note):
                    yield event
                break
    except Exception as exc:  # noqa: BLE001 — bridge death / connection drop mid-stream
        # The run stream broke (e.g. the cursor-sdk-bridge died, often after an
        # OOM/restart). Turn it into a clean RUN_ERROR so the client gets a
        # terminal event + closed tool spinners instead of a hung "running" + a
        # raw "network error". (CancelledError/GeneratorExit are BaseException
        # and intentionally not caught — those are cooperative cancellation.)
        logger.warning("[CursorAgui] run stream failed run_id=%s: %s", run_id, exc, exc_info=True)
        tr.set_error("The analysis was interrupted before it finished — please try again.")
    finally:
        for event in tr.finish():
            yield event
