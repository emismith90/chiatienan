"""Per-room long-term memory files (no LLM here).

Two files live under ``{DATA_DIR}/rooms/{room_id}/``:

- ``memory.md``       — human-readable summary sections, appended over time.
- ``memory.meta.json``— ``{"summarized_through_id": int, "summarized_through_at": str}``.

The ``summarized_through_id`` watermark is the lower bound of the recent-message
window fed to the agent (:mod:`app.chat`). Both ``/clear`` and the 10-week
rollover advance it. All writes happen under ``chat._agent_lock`` (single writer).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
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


def read_meta(room_id: int) -> dict:
    """The watermark file as a dict, or ``{}`` when absent/unreadable.

    Read-only for everyone but :func:`set_watermark`. ``summarized_through_id`` is
    the lower bound of the agent's recent-message window, so moving it backwards
    re-summarizes months of chat into duplicate sections on the next turn — an
    LLM-billed, user-visible mistake with no undo. Nothing in the HTTP surface
    writes it.
    """
    path = room_memory_dir(room_id) / _META_NAME
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return meta if isinstance(meta, dict) else {}


def read_watermark(room_id: int) -> int:
    try:
        return int(read_meta(room_id).get("summarized_through_id", 0))
    except (ValueError, TypeError):
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


# ------------------------------------------------------------------ sections

#: A summary section header, as :func:`append_summary` writes it.
_SECTION_RE = re.compile(r"^## (.*)$", re.M)

#: What ``append_summary`` puts between a section's title and its date.
_DATE_SEP = " — "


def file_etag(room_id: int) -> str:
    """Fingerprint of ``memory.md``, for optimistic concurrency (see observations)."""
    path = room_memory_dir(room_id) / _MD_NAME
    data = path.read_bytes() if path.exists() else b""
    return hashlib.sha256(data).hexdigest()[:16]


def _split(text: str) -> tuple[str, list[tuple[str, str]]]:
    """``(preamble, [(header_line, body)])`` such that the pieces re-concatenate
    into ``text`` byte-for-byte.

    ``body`` keeps its own leading and trailing newlines, which is what makes the
    round-trip exact: a parser that strips whitespace and a writer that re-adds
    its own guess will reformat the whole file the first time anyone edits one
    section of it.
    """
    marks = list(_SECTION_RE.finditer(text))
    if not marks:
        return text, []
    preamble = text[: marks[0].start()]
    blocks = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        blocks.append((m.group(1), text[m.end():end]))
    return preamble, blocks


def _join(preamble: str, blocks: list[tuple[str, str]]) -> str:
    return preamble + "".join(f"## {header}{body}" for header, body in blocks)


def parse_sections(room_id: int) -> list[dict]:
    """``memory.md`` as displayable sections, newest last (file order).

    Each section is ``{"index", "title", "date", "body"}``. ``append_summary``
    writes ``## {header} — {date}``, so the two are split back apart here rather
    than in the UI; a header that doesn't carry a date keeps ``date: None`` instead
    of being dropped, because a hand-written section is still memory.
    """
    path = room_memory_dir(room_id) / _MD_NAME
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    _preamble, blocks = _split(text)
    out = []
    for i, (header, body) in enumerate(blocks):
        title, date_str = header, None
        if _DATE_SEP in header:
            title, date_str = header.rsplit(_DATE_SEP, 1)
        out.append({
            "index": i,
            "title": title.strip(),
            "date": (date_str or "").strip() or None,
            "body": body.strip("\n"),
        })
    return out


class MemoryError_(Exception):
    """A section edit that cannot be applied."""


def write_section(room_id: int, index: int, body: str) -> None:
    """Replace one section's body, leaving every other byte of the file alone."""
    if "\n## " in f"\n{body}":
        # One section may not smuggle in another: the index of every section after
        # it would shift under the client that just edited this one.
        raise MemoryError_("The body must not contain a new section heading (## …).")
    path = room_memory_dir(room_id) / _MD_NAME
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    preamble, blocks = _split(text)
    if not 0 <= index < len(blocks):
        raise MemoryError_("No such section.")
    header, old = blocks[index]
    # Keep the original run of trailing newlines — that run is the blank line
    # separating this section from the next one.
    trailing = old[len(old.rstrip("\n")):] or "\n"
    blocks[index] = (header, "\n" + body.strip() + trailing)
    path.write_text(_join(preamble, blocks), encoding="utf-8")


def delete_section(room_id: int, index: int) -> None:
    """Drop one section, header and body. The watermark is untouched (see
    :func:`read_meta`): deleting a summary must not make the agent re-summarize
    the conversation it came from."""
    path = room_memory_dir(room_id) / _MD_NAME
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    preamble, blocks = _split(text)
    if not 0 <= index < len(blocks):
        raise MemoryError_("No such section.")
    del blocks[index]
    out = _join(preamble, blocks)
    path.write_text(out.rstrip("\n") + "\n" if out.strip() else "", encoding="utf-8")


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
