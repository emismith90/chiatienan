"""Async turn queue — the one place agent runs happen (concurrency 1).

``/api/messages`` acks 200 immediately and enqueues a :class:`TurnJob`; this
single-consumer worker runs the agent to completion and replies proactively via
an injected ``send`` callback. Concurrency 1 both bounds memory (one bridge/turn
at a time) and serialises SQLite writes.

Idempotency: each ``activity_id`` is recorded before the turn runs, so a
re-delivered activity (or a duplicate already sitting in the queue) is dropped
before it can write a second meal (design §3, B1 regression).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app import agent, ledger
from app.db import Database
from app.reply import Reply, build_reply
from app.tools import ToolContext

logger = logging.getLogger("chiatienan")

SendFn = Callable[[Any, Reply], Awaitable[None]]

_FILE_ATTACHMENT_HINT = (
    "Bạn dán ảnh hoá đơn trực tiếp vào khung chat giúp mình nhé "
    "(đừng gửi dưới dạng tệp đính kèm). 🙏"
)
_ERROR_REPLY = "Có lỗi khi xử lý, bạn thử lại sau nhé. 🙏"


@dataclass
class TurnJob:
    activity_id: str
    user_text: str
    sender_id: str | None = None
    sender_name: str | None = None
    sender_aad: str | None = None
    people_mentions: list[dict] = field(default_factory=list)
    images: list[dict] | None = None
    has_file_attachment: bool = False
    conversation_reference: Any = None


class Worker:
    def __init__(self, db: Database, send: SendFn) -> None:
        self.db = db
        self.send = send
        self.queue: asyncio.Queue[TurnJob] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="chiatienan-worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def enqueue(self, job: TurnJob) -> None:
        await self.queue.put(job)

    async def _run(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self.process(job)
            except Exception:  # noqa: BLE001 — never let the consumer die
                logger.exception("[worker] unhandled error processing %s", job.activity_id)
            finally:
                self.queue.task_done()

    async def process(self, job: TurnJob) -> None:
        # Idempotency: claim the activity id up front (dedup queued duplicates).
        if job.activity_id:
            with self.db.session() as s:
                if ledger.already_processed(s, job.activity_id):
                    logger.info("[worker] dropping duplicate activity %s", job.activity_id)
                    return
                ledger.mark_processed(s, job.activity_id)

        if job.has_file_attachment and not job.images:
            await self.send(job.conversation_reference, Reply(text=_FILE_ATTACHMENT_HINT))
            return

        ctx = ToolContext(
            db=self.db,
            sender_teams_id=job.sender_id,
            sender_name=job.sender_name,
            sender_aad=job.sender_aad,
            turn_mentions=job.people_mentions,
        )
        try:
            result = await agent.run_turn(job.user_text, ctx, images=job.images)
            reply = build_reply(result)
        except Exception:  # noqa: BLE001 — turn failure → graceful fallback reply
            logger.exception("[worker] agent turn failed for %s", job.activity_id)
            reply = Reply(text=_ERROR_REPLY)

        await self.send(job.conversation_reference, reply)
