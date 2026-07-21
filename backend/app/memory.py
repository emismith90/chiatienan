"""Per-room long-term memory files (no LLM here).

Two files live under ``{cursor_workspace}/rooms/{room_id}/``:

- ``memory.md``       — human-readable summary sections, appended over time.
- ``memory.meta.json``— ``{"summarized_through_id": int, "summarized_through_at": str}``.

The ``summarized_through_id`` watermark is the lower bound of the recent-message
window fed to the agent (:mod:`app.chat`). Both ``/clear`` and the 10-week
rollover advance it. All writes happen under ``chat._agent_lock`` (single writer).
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import RoomMessage

_META_NAME = "memory.meta.json"
_MD_NAME = "memory.md"


def _base_dir() -> Path:
    """Workspace root; indirection so tests can redirect memory files."""
    return Path(settings.cursor_workspace)


def room_memory_dir(room_id: int) -> Path:
    d = _base_dir() / "rooms" / str(room_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_memory(room_id: int) -> str:
    path = room_memory_dir(room_id) / _MD_NAME
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_watermark(room_id: int) -> int:
    path = room_memory_dir(room_id) / _META_NAME
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("summarized_through_id", 0))
    except (ValueError, TypeError, json.JSONDecodeError, AttributeError):
        return 0


def set_watermark(room_id: int, *, through_id: int, through_at: str) -> None:
    path = room_memory_dir(room_id) / _META_NAME
    path.write_text(
        json.dumps({"summarized_through_id": through_id, "summarized_through_at": through_at}),
        encoding="utf-8",
    )


def append_summary(room_id: int, *, summary_text: str, through_id: int,
                   through_at: str, header: str) -> None:
    date_str = through_at[:10]
    section = f"\n## {header} — {date_str}\n{summary_text.strip()}\n"
    path = room_memory_dir(room_id) / _MD_NAME
    with path.open("a", encoding="utf-8") as f:
        f.write(section)
    set_watermark(room_id, through_id=through_id, through_at=through_at)


def messages_to_summarize(session, room_id: int, *, watermark: int,
                          older_than=None, before_id=None) -> list[RoomMessage]:
    """Chat rows eligible for summarization: ``id > watermark``, text/bot only,
    ordered by id. ``older_than`` (datetime) keeps only ``created_at <
    older_than`` (rollover); ``before_id`` keeps only ``id < before_id``
    (exclude the triggering ``/clear`` line)."""
    q = (
        select(RoomMessage)
        .where(
            RoomMessage.room_id == room_id,
            RoomMessage.id > watermark,
            RoomMessage.kind.in_(("text", "bot")),
        )
        .order_by(RoomMessage.id)
    )
    if older_than is not None:
        q = q.where(RoomMessage.created_at < older_than)
    if before_id is not None:
        q = q.where(RoomMessage.id < before_id)
    return list(session.scalars(q).all())
