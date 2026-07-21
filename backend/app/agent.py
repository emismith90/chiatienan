"""Cursor SDK orchestration — run one PWA room-chat turn to completion.

Unlike the reference sample (which streams AG-UI/SSE to a web chat), this runs the
agent to completion and assembles ONE result: the final assistant text plus the
**structured results of every tool call**. :mod:`app.chat` renders the bot's
reply in the room from those structured results (never from LLM-transcribed
numbers), so a ``settle_period`` payload's amounts + QR URLs reach the user
exactly as the tool computed them (design D3).

Bridge lifecycle: per-turn ``launch_bridge`` (design §8) with a launch-retry
because the bridge is transiently flaky ("exited before discovery").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.prompt import build_system_prompt
from app.tools import ToolContext, build_tools

logger = logging.getLogger("chiatienan")

_LAUNCH_RETRIES = 3
_LAUNCH_BACKOFF = 1.5  # seconds, exponential


@dataclass
class ToolInvocation:
    name: str
    args: dict | None
    result: object


@dataclass
class TurnResult:
    final_text: str = ""
    tools: list[ToolInvocation] = field(default_factory=list)
    error: str | None = None
    turn_id: str | None = None

    def last_result(self, name: str) -> dict | None:
        """Most-recent successful (``ok``) result dict for a given tool name."""
        for inv in reversed(self.tools):
            if inv.name == name and isinstance(inv.result, dict) and inv.result.get("ok"):
                return inv.result
        return None


# --------------------------------------------------------------------------- #
# Cursor message unwrapping (custom/MCP tools surface under name=='mcp')
# --------------------------------------------------------------------------- #

def _unwrap_tool_name(name, args) -> str:
    if name == "mcp" and isinstance(args, dict) and args.get("toolName"):
        return str(args["toolName"])
    return name or "tool"


def _unwrap_tool_args(args):
    if isinstance(args, dict) and "args" in args and args.get("toolName"):
        return args["args"]
    return args


def _unwrap_tool_result(result):
    """Flatten Cursor's result into a Python object.

    A local ``CustomTool`` returns a dict; depending on the bridge it may arrive
    as that dict directly or wrapped in an MCP envelope
    ``{value:{content:[{text:{text:"<json>"}}]}}``. Return a dict when we can
    recover one, else the raw value.
    """
    if result is None:
        return None
    if isinstance(result, dict) and "value" not in result and "content" not in result:
        return result  # already the tool's dict
    text = _flatten_envelope(result)
    if isinstance(text, str):
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text
    return result


def _flatten_envelope(result):
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        value = result.get("value", result)
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        t = block.get("text")
                        if isinstance(t, dict) and isinstance(t.get("text"), str):
                            texts.append(t["text"])
                        elif isinstance(t, str):
                            texts.append(t)
                if texts:
                    return "\n".join(texts)
    return None


def _assistant_text(msg) -> str:
    message = getattr(msg, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    out = []
    for block in content or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str):
                out.append(text)
    return "".join(out)


def _render_prompt(user_text: str, *, sender_name: str | None = None,
                   memory: str | None = None, history: str | None = None) -> str:
    """Assemble the turn preamble. With no memory/history this is byte-identical
    to the pre-memory assembly (system prompt + user message)."""
    sections = [build_system_prompt(sender_name=sender_name)]
    if memory:
        sections.append(f"# Bộ nhớ dài hạn\n{memory.strip()}")
    if history:
        sections.append(f"# Lịch sử hội thoại (gần đây)\n{history.strip()}")
    sections.append(f"# Tin nhắn người dùng\n{user_text.strip()}")
    return "\n\n".join(sections)


def _ensure_workspace() -> str:
    workspace = settings.cursor_workspace
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(workspace, ".cursor-store")).mkdir(parents=True, exist_ok=True)
    return workspace


async def _launch_bridge_resilient(AsyncClient, workspace, local):
    """Retry ``launch_bridge`` — the bridge occasionally exits before discovery."""
    last_exc: Exception | None = None
    for attempt in range(1, _LAUNCH_RETRIES + 1):
        try:
            return await AsyncClient.launch_bridge(workspace=workspace, local=local)
        except Exception as exc:  # noqa: BLE001 — transient launch flake
            last_exc = exc
            logger.warning("[agent] launch_bridge attempt %d/%d failed: %s", attempt, _LAUNCH_RETRIES, exc)
            if attempt < _LAUNCH_RETRIES:
                await asyncio.sleep(_LAUNCH_BACKOFF ** attempt)
    raise RuntimeError(f"launch_bridge failed after {_LAUNCH_RETRIES} attempts") from last_exc


def _build_message(text: str, images):
    if not images:
        return text
    from cursor_sdk import SDKImage, UserMessage

    return UserMessage(
        text=text,
        images=[SDKImage.data_image(img["data"], img["mimeType"]) for img in images],
    )


async def run_turn(user_text: str, ctx: ToolContext, images=None, emit=None,
                    memory=None, history=None) -> TurnResult:
    """Run one turn to completion and return the assembled :class:`TurnResult`.

    ``emit`` — optional ``Callable[[dict], Awaitable[None]]`` — receives the
    live ``agent.*`` timeline events (:mod:`app.agui`) for this turn's SSE
    stream. When ``emit is None`` this function's behavior is unchanged from
    before streaming was added.
    """
    from cursor_sdk import (
        AgentOptions,
        AsyncClient,
        LocalAgentOptions,
        LocalSendOptions,
        SendOptions,
    )

    from app.cursor_runner import (
        default_cursor_model,
        format_cursor_agent_failure,
        resolve_cursor_api_key,
        resolve_model_selection,
    )

    import uuid

    from app import agui

    turn_id = uuid.uuid4().hex
    result = TurnResult()
    max_tools, max_seconds = settings.max_tools, settings.max_seconds
    started = time.monotonic()
    completed_tools = 0
    text_parts: list[str] = []

    async def _emit(events) -> None:
        if emit:
            for ev in events:
                await emit(ev)

    try:
        await _emit(agui.start(turn_id))

        workspace = _ensure_workspace()
        api_key = resolve_cursor_api_key()
        selection = await asyncio.to_thread(
            resolve_model_selection, api_key, default_cursor_model(), reasoning="medium"
        )

        message_text = _render_prompt(user_text, sender_name=ctx.sender_name,
                                       memory=memory, history=history)

        local = LocalAgentOptions(
            cwd=workspace,
            custom_tools=build_tools(ctx),
            store={"type": "sqlite", "root_dir": os.path.join(workspace, ".cursor-store")},
        )
        options = AgentOptions(model=selection, api_key=api_key, local=local, mcp_servers={})
        message = _build_message(message_text, images)

        client = await _launch_bridge_resilient(AsyncClient, workspace, local)
        async with client:
            async with await client.agents.create(options) as agent:
                run = await agent.send(
                    message, SendOptions(model=selection, local=LocalSendOptions(force=True))
                )
                async for msg in run.messages():
                    await _emit(agui.translate(msg, turn_id))
                    mtype = getattr(msg, "type", None)
                    if mtype == "assistant":
                        text_parts.append(_assistant_text(msg))
                    elif mtype == "tool_call":
                        status = (getattr(msg, "status", "") or "").lower()
                        if status in ("completed", "error"):
                            raw_args = getattr(msg, "args", None)
                            name = _unwrap_tool_name(getattr(msg, "name", None), raw_args)
                            result.tools.append(
                                ToolInvocation(
                                    name=name,
                                    args=_unwrap_tool_args(raw_args),
                                    result=_unwrap_tool_result(getattr(msg, "result", None)),
                                )
                            )
                            completed_tools += 1
                    elif mtype == "status":
                        if (getattr(msg, "status", "") or "").upper() == "ERROR":
                            result.error = getattr(msg, "message", "") or "Cursor agent run failed"

                    elapsed = time.monotonic() - started
                    if (max_tools and completed_tools >= max_tools) or (max_seconds and elapsed >= max_seconds):
                        logger.warning(
                            "[agent] turn cap hit tools=%d elapsed=%.0fs", completed_tools, elapsed
                        )
                        if hasattr(run, "supports") and run.supports("cancel"):
                            try:
                                run.cancel()
                            except Exception:  # noqa: BLE001
                                logger.warning("[agent] run.cancel() failed", exc_info=True)
                        break
    except Exception as exc:  # noqa: BLE001 — bridge death / API failure
        logger.error("[agent] run_turn failed: %s", exc, exc_info=True)
        try:
            result.error = format_cursor_agent_failure(exc)
        except Exception:  # noqa: BLE001
            result.error = str(exc)

    result.final_text = "".join(text_parts).strip()

    await _emit(agui.finish(turn_id, error=result.error))
    result.turn_id = turn_id

    return result
