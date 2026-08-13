"""Summarize a chunk of room conversation into durable ``memory.md`` text.

One RPC command: send text, get text. Advisory only — the summary is context for
future turns, NEVER a source of money numbers (design D3), and the prompt says so
explicitly.
"""
from __future__ import annotations

import logging
import uuid

from app.config import settings

logger = logging.getLogger("chiatienan")

_SUMMARY_PROMPT = (
    "Bạn đang tóm tắt lịch sử một nhóm chat chia tiền ăn trưa để làm bộ nhớ dài hạn.\n"
    "Tóm tắt NGẮN GỌN bằng tiếng Việt, 5–10 gạch đầu dòng: các bữa ăn đã ghi, ai trả, "
    "ai nợ ai, các quyết định và ngữ cảnh đáng nhớ.\n"
    "TUYỆT ĐỐI KHÔNG bịa hay tự tính số tiền — chỉ ghi lại con số đã xuất hiện rõ trong "
    "hội thoại. Đây chỉ là bộ nhớ tham khảo, không phải sổ cái.\n\n"
    "# Hội thoại cần tóm tắt\n"
)


async def summarize_messages(rendered_history: str, *, kind: str = "clear") -> str:
    """Fold a rendered conversation into a memory summary. One RPC command.

    **The summarize session is specified, not inherited.** The old implementation
    ran with ``custom_tools=[]``, no ``setting_sources`` and the summary prompt as
    the entire message — no system prompt, no skills, no rules. ``main.js``'s
    ``summarize`` handler reproduces exactly that, because if it silently inherited
    the ``run`` session's construction every room's long-term memory would change
    flavor with no test catching it.

    **Any** failure returns ``""``: a failed summary must never crash a turn, and
    :func:`app.chat._maybe_rollover` leaves the watermark untouched on a blank
    result so the aged messages are retried next turn rather than silently dropped.
    """
    if not rendered_history.strip():
        return ""

    from app.pi_bridge import get_bridge

    req_id = f"sum-{uuid.uuid4().hex[:8]}"
    try:
        bridge = get_bridge()
        async for message in bridge.request({
            "type": "summarize",
            "req_id": req_id,
            "text": _SUMMARY_PROMPT + rendered_history,
            "model": settings.pi_model,
            "thinking": settings.pi_thinking,
            "max_seconds": settings.pi_max_seconds,
        }):
            if message.get("type") == "summarize_done":
                text = (message.get("text") or "").strip()
                if message.get("error"):
                    logger.warning("[summarize] %s failed: %s", kind, message["error"])
                return text
            if message.get("type") == "fatal":
                logger.warning("[summarize] %s fatal: %s", kind, message.get("message"))
                return ""
    except Exception as exc:  # noqa: BLE001 — never let a summary crash a turn
        logger.warning("[summarize] %s failed: %s", kind, exc)
    return ""
