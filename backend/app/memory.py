"""Per-room long-term memory files (no LLM here).

Two files live under ``{DATA_DIR}/rooms/{room_id}/``:

- ``memory.md``       — human-readable summary sections, appended over time.
- ``memory.meta.json``— ``{"summarized_through_id": int, "summarized_through_at": str}``.

The ``summarized_through_id`` watermark is the lower bound of the recent-message
window fed to the agent (:mod:`app.chat`). Both ``/clear`` and the 10-week
rollover advance it. All writes happen under ``chat._agent_lock`` (single writer).
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import RoomMessage

logger = logging.getLogger("chiatienan")

_META_NAME = "memory.meta.json"
_MD_NAME = "memory.md"


#: Where room memory lived before the DATA_DIR rename. Production's Cursor
#: workspace was `/data/cursor-agent` (deploy.yml:164), so pointing memory at
#: `/data/rooms/{id}` without moving anything would make `load_memory` return ""
#: and `_maybe_rollover` re-summarize every room's entire >10-week history into a
#: fresh memory.md on the first post-deploy turn — silent, expensive, user-visible
#: data loss dressed up as a rename.
#:
#: DELETE THIS AND `_migrate_legacy_room` one release after the Pi cutover ships.
_LEGACY_BASE = Path("/data/cursor-agent")


def _base_dir() -> Path:
    """Workspace root; indirection so tests can redirect memory files."""
    return Path(settings.data_dir)


def _migrate_legacy_room(room_id: int, target: Path) -> None:
    """Copy a room's memory over from the pre-DATA_DIR location, once.

    Idempotent and non-destructive: it runs only when the new directory holds no
    `memory.md`, and it **copies** rather than moves, so a rollback to the Cursor
    engine still finds the originals. The repo already does startup migrations of
    this shape (commit `aa1f992`).
    """
    legacy = _LEGACY_BASE / "rooms" / str(room_id)
    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return
    if (target / _MD_NAME).exists():
        return                      # newer memory already lives here; never clobber
    for name in (_MD_NAME, _META_NAME):
        source = legacy / name
        if source.exists():
            shutil.copy2(source, target / name)
            logger.info("[memory] migrated %s for room %s from %s", name, room_id, legacy)


def room_memory_dir(room_id: int) -> Path:
    d = _base_dir() / "rooms" / str(room_id)
    d.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_room(room_id, d)
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
