"""Run one PWA room-chat turn to completion, through the Pi sidecar.

Assembles ONE result: the final assistant text plus the **structured results of
every tool call**. :mod:`app.chat` renders the bot's reply from those structured
results (never from LLM-transcribed numbers), so a ``settle_period`` payload's
amounts and QR URLs reach the user exactly as the tool computed them (design D3).

**This module is a shim, deliberately.** It builds a command, forwards events,
executes tools, and hydrates a dataclass. Everything about how pi behaves — model
resolution, event shapes, turn caps, answer assembly, narration stripping, error
formatting — lives in ``agent_sidecar/`` (design §3.1). If a change to this file
needs to know something about pi, it belongs in ``turn.js``.

``run_turn``'s signature and ``TurnResult``'s shape are **frozen**: 14
``monkeypatch.setattr`` sites across 4 test files depend on them, and
``chat.py:503`` is the only production caller.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.prompt import build_system_prompt
from app.tools import ToolContext, build_tools, tool_manifest

logger = logging.getLogger("chiatienan")

#: Skill bodies and the always-on rules file ship as text, so nothing is written
#: to disk. See ``session.js``: pi's skill mechanism needs a real file and defers
#: the body to ``read``, which the sidecar disables — so bodies go out as context
#: files instead.
_SKILLS_DIR = Path(__file__).resolve().parent / "agent_skills" / "skills"
_RULES_DIR = Path(__file__).resolve().parent / "agent_skills" / "rules"


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

    def all_results(self, name: str) -> list[dict]:
        """All successful (``ok``) result dicts for a tool name, in call order."""
        return [inv.result for inv in self.tools
                if inv.name == name and isinstance(inv.result, dict) and inv.result.get("ok")]


def _render_prompt(user_text: str, *, sender_name: str | None = None,
                   memory: str | None = None, history: str | None = None,
                   image_count: int = 0) -> str:
    """Assemble the turn's user message.

    ``image_count`` is announced in the text because the images themselves ride on
    the message, invisible to the prompt: in production the bill was attached and
    the model still asked for the total that was in it, then read that same total
    off the image one turn later. The history renders past images as ``[ảnh: N]``
    for the same reason — this covers the current turn.

    The system prompt is **no longer prepended** here: the sidecar has a real
    ``systemPromptOverride``, so ``build_system_prompt`` goes out as ``system``.
    """
    sections = []
    if memory:
        sections.append(f"# Bộ nhớ dài hạn\n{memory.strip()}")
    if history:
        sections.append(f"# Lịch sử hội thoại (gần đây)\n{history.strip()}")
    if image_count:
        sections.append(
            f"# Ảnh kèm theo\nLượt này có {image_count} ảnh (thường là hoá đơn) — "
            "ĐỌC ảnh trước khi trả lời. Đừng hỏi lại tổng tiền / giá từng món nếu ảnh đã có."
        )
    sections.append(f"# Tin nhắn người dùng\n{user_text.strip()}")
    return "\n\n".join(sections)


def _read_skills() -> list[dict]:
    """The four ``SKILL.md`` bodies, as text. Their frontmatter is already
    ``name`` + ``description``, which is exactly what pi wants."""
    skills = []
    if not _SKILLS_DIR.is_dir():
        return skills
    for directory in sorted(_SKILLS_DIR.iterdir()):
        path = directory / "SKILL.md"
        if path.is_file():
            skills.append({"name": directory.name, "description": "",
                           "body": path.read_text(encoding="utf-8")})
    return skills


def _read_context_files() -> list[dict]:
    """``money-safety.mdc`` had no pi always-apply equivalent, so it ships as a
    context file — which pi loads into every system prompt."""
    files = []
    for path in sorted(_RULES_DIR.glob("*.mdc")) if _RULES_DIR.is_dir() else []:
        files.append({"path": path.stem, "content": path.read_text(encoding="utf-8")})
    return files


async def run_turn(user_text: str, ctx: ToolContext, images=None, emit=None,
                   memory=None, history=None) -> TurnResult:
    """Run one turn to completion and return the assembled :class:`TurnResult`.

    ``emit`` — optional ``Callable[[dict], Awaitable[None]]`` — receives the live
    ``agent.*`` timeline events for this turn's SSE stream. The sidecar emits them
    already in the app's wire format, so they are forwarded untouched; the
    74-line translator ``app/agui.py`` used to be is deleted.
    """
    from app.pi_bridge import get_bridge

    turn_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    result = TurnResult(turn_id=turn_id)

    async def _emit(event: dict) -> None:
        if emit:
            await emit(event)

    command = {
        "type": "run",
        "req_id": f"run-{turn_id}",
        "turn_id": turn_id,
        "system": build_system_prompt(sender_name=ctx.sender_name),
        "message": _render_prompt(user_text, sender_name=ctx.sender_name, memory=memory,
                                  history=history, image_count=len(images or [])),
        "images": list(images or []),
        "tools": tool_manifest(),
        "skills": _read_skills(),
        "context_files": _read_context_files(),
        "model": settings.pi_model,
        "vision_model": settings.pi_vision_model,
        "thinking": settings.pi_thinking,
        "max_tools": settings.pi_max_tools,
        "max_seconds": settings.pi_max_seconds,
    }

    tools = build_tools(ctx)
    bridge = get_bridge()
    capped = False

    try:
        async for message in bridge.request(command):
            kind = message.get("type", "")

            if kind.startswith("agent."):
                # Already the frontend's format. Forward, do not interpret.
                await _emit(message)

            elif kind == "tool_call":
                name = message.get("name") or ""
                args = message.get("args") or {}
                tool = tools.get(name)
                if tool is None:
                    payload = {"ok": False, "error": f"unknown tool {name}"}
                else:
                    try:
                        payload = tool.execute(args)
                    except Exception as exc:  # noqa: BLE001 — a tool must not kill the turn
                        logger.exception("[agent] tool %s raised", name)
                        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                result.tools.append(ToolInvocation(name=name, args=args, result=payload))
                await bridge.send({
                    "type": "tool_result", "req_id": command["req_id"],
                    "call_id": message.get("call_id"),
                    "content": _json_dumps(payload),
                })

            elif kind == "turn_done":
                result.final_text = message.get("final_text") or ""
                result.error = message.get("error")
                capped = bool(message.get("capped"))
                # `tools` is already accumulated from the round-trips above, which
                # is the authoritative list: those results came from the real DB.

            elif kind == "fatal":
                result.error = message.get("message") or "sidecar failed"
                await _emit({"type": "agent.run.error", "turn_id": turn_id,
                             "message": result.error})
    except Exception as exc:  # noqa: BLE001 — a failed turn must still be a reply
        logger.exception("[agent] turn %s failed", turn_id)
        result.error = f"{type(exc).__name__}: {exc}"
        await _emit({"type": "agent.run.error", "turn_id": turn_id, "message": result.error})

    # One line per turn so the log mirror (and /internal/debug/logs) shows where a
    # 20–80s turn actually went: how many tool round-trips, and which. The agent.*
    # timeline has this live but never persists it.
    logger.info(
        "[agent] turn %s done in %.1fs tools=%d (%s) images=%d text=%dch%s%s",
        turn_id, time.monotonic() - started, len(result.tools),
        ",".join(inv.name for inv in result.tools) or "-",
        len(images or []), len(result.final_text),
        " CAPPED" if capped else "",
        f" ERROR={result.error}" if result.error else "",
    )
    return result


def _json_dumps(payload) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False, default=str)
