"""Cursor SDK bridge smoke test — validates B3 (does cursor-sdk-bridge run here?).

Launches the bridge subprocess, creates an agent, sends one trivial prompt, and
reports whether the round-trip succeeds. Uses ``ModelSelection(id="auto")`` so it
does NOT depend on the model-resolution layer (bare parameterized ids like
``composer-2.5`` fail; ``auto`` runs fine) — this test only proves the bridge
launches and a run completes inside the container.

Consumed by the guarded ``POST /internal/bridge-smoke`` route. Delete once the
real agent path lands, or keep as an ops liveness check.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from app.config import settings


def _extract_text(msg: object) -> str:
    """Best-effort assistant-text pull from a Cursor run message. Never raises."""
    try:
        role = getattr(msg, "role", None)
        if role is None and isinstance(msg, dict):
            role = msg.get("role")
        if role and role != "assistant":
            return ""
        message = getattr(msg, "message", None)
        if message is None and isinstance(msg, dict):
            message = msg.get("message") or msg
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, (list, tuple)):
            out = []
            for block in content:
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text")
                if isinstance(text, dict):
                    text = text.get("text")
                if isinstance(text, str):
                    out.append(text)
            return "".join(out)
    except Exception:  # noqa: BLE001 — smoke test extraction must never crash the run
        return ""
    return ""


async def run_bridge_smoke(prompt: str = "Reply with the single word: pong.") -> dict:
    from cursor_sdk import (
        AgentOptions,
        AsyncClient,
        LocalAgentOptions,
        LocalSendOptions,
        ModelSelection,
        SendOptions,
    )

    if not settings.cursor_api_key:
        return {"ok": False, "error": "CURSOR_API_KEY not set"}

    ws = settings.cursor_workspace
    store = os.path.join(ws, ".cursor-store")
    Path(ws).mkdir(parents=True, exist_ok=True)
    Path(store).mkdir(parents=True, exist_ok=True)

    selection = ModelSelection(id="auto")
    local = LocalAgentOptions(
        cwd=ws,
        custom_tools={},
        store={"type": "sqlite", "root_dir": store},
    )
    options = AgentOptions(
        model=selection, api_key=settings.cursor_api_key, local=local, mcp_servers={}
    )

    started = time.monotonic()
    messages_seen = 0
    texts: list[str] = []
    try:
        async with await AsyncClient.launch_bridge(workspace=ws, local=local) as client:
            async with await client.agents.create(options) as agent:
                run = await agent.send(
                    prompt, SendOptions(model=selection, local=LocalSendOptions(force=True))
                )
                async for msg in run.messages():
                    messages_seen += 1
                    t = _extract_text(msg)
                    if t:
                        texts.append(t)
                    if messages_seen > 200 or (time.monotonic() - started) > 120:
                        break
    except Exception as exc:  # noqa: BLE001 — report failure, don't crash the endpoint
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.monotonic() - started, 1),
            "messages_seen": messages_seen,
        }

    return {
        "ok": True,
        "elapsed_s": round(time.monotonic() - started, 1),
        "messages_seen": messages_seen,
        "text": " ".join(texts).strip()[:500],
    }
