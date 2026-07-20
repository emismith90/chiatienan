"""Cursor-SDK-backed agent (decoupled sample — static prompt + demo tools).

Composition (all reused/tested pieces):
  - model wiring    → ``cursor_runner.resolve_model_selection``
  - local tools     → ``tools.build_demo_tools`` (demo get_current_time tool)
  - prompt          → ``prompt.build_system_prompt`` (static system prompt)
  - streaming       → ``cursor_agui.cursor_run_to_agui`` (Cursor stream → AG-UI)

Attach a real MCP server by uncommenting the mcp_servers= stub in tools.py
and passing the result to AgentOptions (see the comment in this file).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from app.config import settings

def _store_root(workspace: str) -> str:
    return os.path.join(workspace, ".cursor-store")


def _ensure_workspace() -> str:
    workspace = settings.cursor_workspace
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(_store_root(workspace)).mkdir(parents=True, exist_ok=True)
    return workspace


def _message_role_content(m: Any) -> tuple[str | None, str | None]:
    if isinstance(m, dict):
        return m.get("role"), m.get("content")
    return getattr(m, "role", None), getattr(m, "content", None)


def _render_prompt(system_prompt: str, messages: list[Any]) -> str:
    """v1 prompt: system preamble + the resent transcript.

    Cursor's Agent has no ``instructions`` field, so the dynamic system prompt is
    sent as a preamble. History is included inline until the Postgres store lands.
    """
    parts = [system_prompt.strip(), "\n\n# Conversation"]
    for m in messages:
        role, content = _message_role_content(m)
        if not role or not content or role == "system":
            continue
        parts.append(f"\n## {role}\n{content}")
    parts.append("\n\n# Task\nRespond to the latest user message above.")
    return "\n".join(parts)


def _build_send_message(prompt: str, images: list[dict[str, str]] | None):
    """Build the ``agent.send`` payload.

    Text-only turns send the rendered prompt string. When the turn carries image
    attachments, wrap it in a multimodal ``UserMessage`` so a vision-capable model
    (composer-2.5) sees them. ``images`` is a list of
    ``{"data": <raw base64>, "mimeType": <"image/png"|...>}`` (already sanitized).
    """
    if not images:
        return prompt
    from cursor_sdk import SDKImage, UserMessage

    return UserMessage(
        text=prompt,
        images=[SDKImage.data_image(img["data"], img["mimeType"]) for img in images],
    )


async def run_agent_cursor(
    run_input,
    *,
    model_override: str | None = None,
    images: list[dict[str, str]] | None = None,
):
    """Async generator of AG-UI events for one chat turn (Cursor SDK backend).

    ``images`` (when present) are this turn's attachments — a list of
    {"data": <base64>, "mimeType": ...} dicts already sanitized by the route.
    They attach to THIS send only; history is replayed as plain text, so a
    vision-capable model (composer-2.5) reads them on this turn.
    """
    from cursor_sdk import (
        AgentOptions,
        AsyncClient,
        LocalAgentOptions,
        LocalSendOptions,
        SendOptions,
    )

    from app.cursor_agui import cursor_run_to_agui
    from app.cursor_runner import default_cursor_model, resolve_cursor_api_key, resolve_model_selection
    from app.prompt import build_system_prompt
    from app.tools import build_demo_tools

    workspace = _ensure_workspace()
    model_name = model_override or default_cursor_model()
    api_key = resolve_cursor_api_key()
    selection = resolve_model_selection(api_key, model_name, reasoning="medium")

    prompt = _render_prompt(build_system_prompt(), list(run_input.messages))

    local = LocalAgentOptions(
        cwd=workspace,
        custom_tools=build_demo_tools(),
        store={"type": "sqlite", "root_dir": _store_root(workspace)},
    )
    # To attach an MCP server, pass mcp_servers=build_mcp_servers() here (see tools.py).
    options = AgentOptions(model=selection, api_key=api_key, local=local, mcp_servers={})
    message = _build_send_message(prompt, images)

    async with await AsyncClient.launch_bridge(workspace=workspace, local=local) as client:
        async with await client.agents.create(options) as agent:
            run = await agent.send(
                message, SendOptions(model=selection, local=LocalSendOptions(force=True))
            )
            error_hint = (
                f"model '{selection.id}' could not run via the Cursor SDK. Some models "
                "aren't available on the local bridge for every plan — composer-2.5 is "
                "known to work, and image attachments need a vision-capable model."
            )
            async for event in cursor_run_to_agui(
                run, run_input.thread_id, run_input.run_id, error_hint=error_hint
            ):
                yield event
