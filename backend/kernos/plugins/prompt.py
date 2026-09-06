"""Host-agnostic ``prompt`` plugin: the sectioned user message."""
from __future__ import annotations

from kernos.kernel.context import Stage, TurnContext
from kernos.kernel.plugin import BasePlugin

DEFAULT_HEADERS = {
    "memory": "# Bộ nhớ dài hạn",
    "history": "# Lịch sử hội thoại (gần đây)",
    "images": "# Ảnh kèm theo",
    "user": "# Tin nhắn người dùng",
}
DEFAULT_IMAGES_NOTE = (
    "Lượt này có {n} ảnh (thường là hoá đơn) — "
    "ĐỌC ảnh trước khi trả lời. Đừng hỏi lại tổng tiền / giá từng món nếu ảnh đã có."
)


def render_sections(user_text: str, *, memory: str | None = None, history: str | None = None,
                    image_count: int = 0, headers: dict | None = None,
                    images_note: str = DEFAULT_IMAGES_NOTE) -> str:
    """Assemble the turn's user message.

    ``image_count`` is announced in the text because the images themselves ride on
    the message, invisible to the prompt: in production the bill was attached and
    the model still asked for the total that was in it, then read that same total
    off the image one turn later. The history renders past images as ``[ảnh: N]``
    for the same reason — this covers the current turn.
    """
    h = {**DEFAULT_HEADERS, **(headers or {})}
    sections = []
    if memory:
        sections.append(f"{h['memory']}\n{memory.strip()}")
    if history:
        sections.append(f"{h['history']}\n{history.strip()}")
    if image_count:
        sections.append(f"{h['images']}\n{images_note.format(n=image_count)}")
    sections.append(f"{h['user']}\n{user_text.strip()}")
    return "\n\n".join(sections)


class SectionsMessage(BasePlugin):
    id, version, stage = "kernos.prompt.sections", "1", Stage.prompt
    config_schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
            "images_note": {"type": "string"},
        },
    }

    async def run(self, ctx: TurnContext, config: dict) -> None:
        ctx.message = render_sections(
            ctx.text, memory=ctx.memory, history=ctx.history, image_count=len(ctx.images or []),
            headers=config.get("headers"), images_note=config.get("images_note", DEFAULT_IMAGES_NOTE))
