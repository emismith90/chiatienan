"""In-memory implementations of every host adapter — for tests and the example host."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from kernos.kernel.events import TurnEvent, to_legacy


@dataclass
class Message:
    id: int
    space_id: str
    author: Any
    kind: str
    body: str
    attachments: dict | None
    created_at: datetime


class InMemoryHistory:
    def __init__(self) -> None:
        self._messages: dict[str, list[Message]] = {}
        self._ids = itertools.count(1)

    def add(self, space_id: str, *, author: Any, body: str, kind: str = "text",
            attachments: dict | None = None, created_at: datetime | None = None) -> Message:
        m = Message(next(self._ids), space_id, author, kind, body, attachments,
                    created_at or datetime.now(timezone.utc))
        self._messages.setdefault(space_id, []).append(m)
        return m

    def _rows(self, space_id: str, *, since_id: int, before_id: int | None) -> list[Message]:
        return [m for m in self._messages.get(space_id, [])
                if m.id > since_id and (before_id is None or m.id < before_id)
                and m.kind in ("text", "bot")]

    def render(self, space_id, *, bot_label, since_id, limit, before_id) -> str:
        rows = self._rows(space_id, since_id=since_id, before_id=before_id)[-limit:]
        return "\n".join(
            f"{bot_label}: {m.body}" if m.author is None else f"«{m.author}»: {m.body}" for m in rows)

    def recent_images(self, space_id, *, before_id) -> list:
        for m in reversed(self._messages.get(space_id, [])):
            if before_id is not None and m.id >= before_id:
                continue
            images = ((m.attachments or {}).get("images")) or []
            if images:
                return list(images)
        return []

    def aged(self, space_id, *, watermark, older_than) -> tuple[str, int | None]:
        rows = [m for m in self._rows(space_id, since_id=watermark, before_id=None)
                if m.created_at < older_than]
        if not rows:
            return "", None
        return "\n".join(m.body for m in rows), rows[-1].id


@dataclass
class InMemoryMemory:
    _text: dict[str, str] = field(default_factory=dict)
    _wm: dict[str, tuple[int, str]] = field(default_factory=dict)

    def load(self, space_id): return self._text.get(space_id, "")
    def watermark(self, space_id): return self._wm.get(space_id, (0, ""))[0]
    def set_watermark(self, space_id, *, through_id, through_at): self._wm[space_id] = (through_id, through_at)

    def append_summary(self, space_id, *, summary_text, through_id, through_at, header):
        self._text[space_id] = self._text.get(space_id, "") + f"\n## {header} — {through_at[:10]}\n{summary_text.strip()}\n"
        self.set_watermark(space_id, through_id=through_id, through_at=through_at)


class InMemoryKnowledge:
    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {}

    def snapshot(self, space_id): return self._data.get(space_id, {})


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, event: TurnEvent) -> None: self.events.append(to_legacy(event))
    async def emit_raw(self, payload: dict) -> None: self.events.append(payload)


class InMemoryMessages:
    def __init__(self, history: InMemoryHistory) -> None:
        self._history = history

    def post(self, space_id, *, author, kind, body, attachments):
        return self._history.add(space_id, author=author, body=body, kind=kind, attachments=attachments)

    def to_payload(self, message: Message) -> dict:
        return {"id": message.id, "kind": message.kind, "body": message.body,
                "attachments": message.attachments, "author": message.author}


class InMemoryCards:
    """Cards are messages with ``kind`` = the draft kind; a new card of the same kind
    supersedes every pending one — enough for tests of the persist stage."""

    def __init__(self, history: InMemoryHistory) -> None:
        self._history = history

    def create(self, space_id, kind, payload):
        superseded = []
        for m in self._history._messages.get(space_id, []):
            if m.kind == kind and (m.attachments or {}).get("status") == "pending":
                m.attachments = {**m.attachments, "status": "superseded"}
                superseded.append(m)
        card = self._history.add(space_id, author=None, body="", kind=kind,
                                 attachments={"type": kind, "status": "pending", **payload})
        return card, superseded

    def get(self, space_id, card_id):
        return next((m for m in self._history._messages.get(space_id, []) if m.id == card_id), None)

    def pending(self, space_id):
        return [m for m in self._history._messages.get(space_id, [])
                if (m.attachments or {}).get("status") == "pending"]

    def cancel(self, space_id, card_id):
        card = self.get(space_id, card_id)
        if card is None or (card.attachments or {}).get("status") != "pending":
            raise ValueError(f"Draft #{card_id} not found.")
        card.attachments = {**card.attachments, "status": "cancelled"}
        return card


class CannedCompletion:
    def __init__(self, text: str = "") -> None:
        self.text, self.calls = text, []

    async def complete(self, prompt, *, kind):
        self.calls.append((prompt, kind))
        return self.text


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime: return self._at
    def today(self) -> date: return self._at.date()
