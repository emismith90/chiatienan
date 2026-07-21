"""Summarize a chunk of room conversation into durable ``memory.md`` text.

One minimal Cursor call (no custom tools). Advisory only: the summary is context
for future turns, NEVER a source of money numbers (design D3) — the prompt says
so explicitly. Reuses :mod:`app.agent`'s workspace + resilient-launch helpers so
tests mock it exactly like ``run_turn``.
"""
from __future__ import annotations

import asyncio
import logging
import os

from app.agent import _assistant_text, _ensure_workspace, _launch_bridge_resilient

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
    if not rendered_history.strip():
        return ""
    from cursor_sdk import (
        AgentOptions,
        AsyncClient,
        LocalAgentOptions,
        LocalSendOptions,
        SendOptions,
    )
    from app.cursor_runner import (
        default_cursor_model,
        resolve_cursor_api_key,
        resolve_model_selection,
    )

    try:
        workspace = _ensure_workspace()
        api_key = resolve_cursor_api_key()
        selection = await asyncio.to_thread(
            resolve_model_selection, api_key, default_cursor_model(), "medium"
        )
        message_text = _SUMMARY_PROMPT + rendered_history
        local = LocalAgentOptions(
            cwd=workspace,
            custom_tools=[],
            store={"type": "sqlite", "root_dir": os.path.join(workspace, ".cursor-store")},
        )
        options = AgentOptions(model=selection, api_key=api_key, local=local, mcp_servers={})
        parts: list[str] = []
        client = await _launch_bridge_resilient(AsyncClient, workspace, local)
        async with client:
            async with await client.agents.create(options) as agent:
                run = await agent.send(
                    message_text, SendOptions(model=selection, local=LocalSendOptions(force=True))
                )
                async for msg in run.messages():
                    if getattr(msg, "type", None) == "assistant":
                        parts.append(_assistant_text(msg))
        return "".join(parts).strip()
    except Exception:  # noqa: BLE001 — a failed summary must degrade, never crash a turn
        logger.exception("[summarize] kind=%s failed", kind)
        return ""
