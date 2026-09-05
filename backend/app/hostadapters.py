"""chiatienan's implementations of the kernos host adapters (plan Task 1.5).

Pure delegation, no logic: every method is one call into the module that owns
the behaviour today. ``space_id`` is the room id as a string; the room id is the
host's tenant key and the kernel never looks inside it.
"""
from __future__ import annotations

from datetime import date, datetime

from app import chat, drafts, knowledge, memory
from app.clock import now_ict, today_ict
from app.db import Database
from app.models import RoomMessage
from kernos.adapters import HostAdapters


def _room(space_id: str) -> int:
    return int(space_id)


class RoomHistory:
    def __init__(self, db: Database) -> None:
        self._db = db

    def render(self, space_id, *, bot_label, since_id, limit, before_id) -> str:
        # `bot_label` is accepted and ignored until Phase 3: `chat._render_messages`
        # writes the persona label itself today, and changing that here would be a
        # behaviour change Phase 1 promises not to make.
        with self._db.session() as s:
            return chat.build_history(s, _room(space_id), watermark=since_id,
                                      before_id=before_id, limit=limit)

    def recent_images(self, space_id, *, before_id) -> list:
        with self._db.session() as s:
            return chat.recent_images(s, _room(space_id), before_id=before_id)

    def aged(self, space_id, *, watermark, older_than) -> tuple[str, int | None]:
        room_id = _room(space_id)
        with self._db.session() as s:
            rows = memory.messages_to_summarize(s, room_id, watermark=watermark, older_than=older_than)
            if not rows:
                return "", None
            return chat._render_messages(s, room_id, rows), rows[-1].id


class RoomMemory:
    def load(self, space_id) -> str: return memory.load_memory(_room(space_id))
    def watermark(self, space_id) -> int: return memory.read_watermark(_room(space_id))

    def set_watermark(self, space_id, *, through_id, through_at) -> None:
        memory.set_watermark(_room(space_id), through_id=through_id, through_at=through_at)

    def append_summary(self, space_id, *, summary_text, through_id, through_at, header) -> None:
        memory.append_summary(_room(space_id), summary_text=summary_text, through_id=through_id,
                              through_at=through_at, header=header)


class RoomKnowledge:
    def __init__(self, db: Database) -> None:
        self._db = db

    def snapshot(self, space_id) -> dict:
        with self._db.session() as s:
            return knowledge.snapshot(s, _room(space_id))


class RoomMessages:
    def __init__(self, db: Database) -> None:
        self._db = db

    def post(self, space_id, *, author, kind, body, attachments) -> RoomMessage:
        with self._db.session() as s:
            return chat.post_message(s, _room(space_id), author, body, attachments=attachments, kind=kind)

    def to_payload(self, message: RoomMessage) -> dict:
        return chat.message_to_dict(message, None)


class RoomCards:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, space_id, kind, payload) -> tuple[RoomMessage, list[RoomMessage]]:
        with self._db.session() as s:
            if kind == "expense_draft":
                return drafts.create_draft(s, _room(space_id), payload)
            if kind == "payment_draft":
                return drafts.create_payment_draft(s, _room(space_id), payload)
            raise ValueError(f"unknown draft kind {kind!r}")

    def get(self, space_id, card_id) -> RoomMessage | None:
        with self._db.session() as s:
            card = s.get(RoomMessage, card_id) if card_id else None
            return card if card is not None and card.room_id == _room(space_id) else None


class ChatCompletion:
    """The summariser, looked up on ``app.chat`` at call time so the tests that
    patch ``app.chat.summarize_messages`` keep intercepting it."""

    async def complete(self, prompt, *, kind) -> str:
        return await chat.summarize_messages(prompt, kind=kind)


class IctClock:
    def now(self) -> datetime: return now_ict()
    def today(self) -> date: return today_ict()


def build_adapters(db: Database) -> HostAdapters:
    return HostAdapters(
        history=RoomHistory(db), memory=RoomMemory(), messages=RoomMessages(db),
        clock=IctClock(), knowledge=RoomKnowledge(db), cards=RoomCards(db),
        completion=ChatCompletion(),
    )
