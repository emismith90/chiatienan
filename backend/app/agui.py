"""Cursor run-message → chiatienan ``agent.*`` SSE event translator.

Live-only progress: these events are published to the RoomHub during a turn and
never persisted. The authoritative bot message is still posted separately from
the money-safe TurnResult. Adapted from the Atlas reference cursor_agui.py,
reduced to the plain dict events this PWA's SSE stream carries.
"""
from __future__ import annotations

import json


def _unwrap_name(name, args) -> str:
    if name == "mcp" and isinstance(args, dict) and args.get("toolName"):
        return str(args["toolName"])
    return name or "tool"


def _unwrap_args(args):
    if isinstance(args, dict) and "args" in args and args.get("toolName"):
        return args["args"]
    return args


def _assistant_text(msg) -> str:
    message = getattr(msg, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    out = []
    for block in content or []:
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str):
            out.append(block.text)
    return "".join(out)


def start(turn_id: str) -> list[dict]:
    return [{"type": "agent.run.started", "turn_id": turn_id}]


def finish(turn_id: str, error: str | None = None) -> list[dict]:
    if error:
        return [{"type": "agent.run.error", "turn_id": turn_id, "message": error}]
    return [{"type": "agent.run.finished", "turn_id": turn_id}]


def translate(msg, turn_id: str) -> list[dict]:
    mtype = getattr(msg, "type", None)
    if mtype == "assistant":
        text = _assistant_text(msg)
        return [{"type": "agent.text.delta", "turn_id": turn_id, "delta": text}] if text else []
    if mtype == "tool_call":
        status = (getattr(msg, "status", "") or "").lower()
        call_id = getattr(msg, "call_id", None) or ""
        raw_args = getattr(msg, "args", None)
        name = _unwrap_name(getattr(msg, "name", None), raw_args)
        if status in ("completed", "error"):
            result = getattr(msg, "result", None)
            return [{"type": "agent.tool.result", "turn_id": turn_id, "call_id": call_id,
                     "name": name, "status": status,
                     "result": json.loads(json.dumps(result, default=str)) if result is not None else None}]
        return [{"type": "agent.tool.start", "turn_id": turn_id, "call_id": call_id,
                 "name": name, "args": json.loads(json.dumps(_unwrap_args(raw_args), default=str))}]
    return []
