"""What a host implements so the kernel can run a turn in it (design §4.6).

Nine small protocols. chiatienan implements them over ``chat.build_history``,
``memory.py``, ``knowledge.py``, its SSE hub, ``chat.post_message``,
``drafts.create_*``, ``summarize.summarize_messages`` and ``clock.py``
(``app/hostadapters.py``); :mod:`kernos.adapters.memory` implements them over
dicts for tests and the example host. Everything the kernel hands back to a host
(message refs, card refs) is opaque to the kernel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from kernos.kernel.events import TurnEvent


@runtime_checkable
class HistorySource(Protocol):
    def render(self, space_id: str, *, bot_label: str, since_id: int, limit: int,
               before_id: int | None) -> str:
        """Recent conversation as text, oldest → newest, ``since_id < id [< before_id]``."""
        ...

    def recent_images(self, space_id: str, *, before_id: int | None) -> list:
        """Images attached to a recent message, for the "bill pasted, then asked" case."""
        ...

    def aged(self, space_id: str, *, watermark: int, older_than: datetime) -> tuple[str, int | None]:
        """``(rendered, through_id)`` of messages above ``watermark`` older than the cutoff;
        ``("", None)`` when nothing has aged out."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    def load(self, space_id: str) -> str: ...
    def watermark(self, space_id: str) -> int: ...
    def set_watermark(self, space_id: str, *, through_id: int, through_at: str) -> None: ...
    def append_summary(self, space_id: str, *, summary_text: str, through_id: int,
                       through_at: str, header: str) -> None: ...


@runtime_checkable
class KnowledgeSource(Protocol):
    def snapshot(self, space_id: str) -> Any: ...


@runtime_checkable
class EventSink(Protocol):
    async def emit(self, event: TurnEvent) -> None: ...
    async def emit_raw(self, payload: dict) -> None: ...


@runtime_checkable
class MessageStore(Protocol):
    def post(self, space_id: str, *, author: Any, kind: str, body: str,
             attachments: dict | None) -> Any:
        """Persist a reply; returns a host message ref."""
        ...

    def to_payload(self, message: Any) -> dict:
        """The wire dict for a message ref (what the room's stream publishes)."""
        ...


@runtime_checkable
class CardStore(Protocol):
    def create(self, space_id: str, kind: str, payload: dict) -> tuple[Any, list[Any]]:
        """Persist a draft card; returns ``(card, superseded_cards)``."""
        ...

    def get(self, space_id: str, card_id: Any) -> Any | None: ...


@runtime_checkable
class Completion(Protocol):
    async def complete(self, prompt: str, *, kind: str) -> str:
        """One-shot text → text (the summariser). ``""`` on failure, never a raise."""
        ...


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
    def today(self) -> date: ...


@dataclass
class HostAdapters:
    """Everything a host hands the kernel at composition time. ``sink`` is per turn
    and travels on ``TurnContext`` instead."""

    history: HistorySource
    memory: MemoryStore
    messages: MessageStore
    clock: Clock
    knowledge: KnowledgeSource | None = None
    cards: CardStore | None = None
    completion: Completion | None = None
