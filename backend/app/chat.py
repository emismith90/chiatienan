"""Room chat — persist/list messages, ``@bot`` detection, agent dispatch.

Human messages are appended via :func:`post_message` (called by the route
layer). A message that :func:`mentions_bot` triggers :func:`run_bot_turn`,
which serializes agent runs through a module-level ``asyncio.Lock`` (the
ledger has a single writer — design §3) and posts the reply as a
``kind="bot"`` message, with structured tool results rendered via
:func:`render_bot_attachments` rather than re-parsed from LLM prose (design D3).
"""
from __future__ import annotations

import asyncio
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Database
from app.models import Member, RoomMessage

_agent_lock = asyncio.Lock()  # serialize agent runs (ledger single-writer)


def mentions_bot(text: str) -> bool:
    handle = re.escape(settings.bot_handle)
    return re.search(rf"@(bot|{handle})\b", text or "", re.IGNORECASE) is not None


def message_to_dict(m: RoomMessage, author: Member | None) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "body": m.body,
        "attachments": m.attachments,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "author": None if author is None else {
            "id": author.id, "name": author.display_name, "nickname": author.nickname,
        },
    }


def post_message(session: Session, room_id: int, author_member_id: int | None,
                  body: str, attachments: dict | None = None, kind: str = "text") -> RoomMessage:
    m = RoomMessage(room_id=room_id, author_member_id=author_member_id, kind=kind,
                     body=body, attachments=attachments)
    session.add(m)
    session.flush()
    return m


def list_messages(session: Session, room_id: int, since_id: int = 0, limit: int = 200) -> list[dict]:
    rows = session.scalars(
        select(RoomMessage)
        .where(RoomMessage.room_id == room_id, RoomMessage.id > since_id)
        .order_by(RoomMessage.id)
        .limit(limit)
    ).all()
    authors = {m.id: m for m in session.scalars(select(Member).where(Member.room_id == room_id))}
    return [message_to_dict(r, authors.get(r.author_member_id)) for r in rows]


def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        return {"type": "settlement", **settle}
    meal = result.last_result("record_meal")
    if meal:
        return {"type": "meal", **meal}
    return None


async def run_bot_turn(db: Database, room_id: int, member_id: int, member_name: str,
                        text: str, images=None) -> RoomMessage:
    """Run the agent for one ``@bot`` turn and persist its reply.

    Serialized by ``_agent_lock`` so ledger-writing tool calls (``record_meal``,
    ``settle_period``) from concurrent turns never interleave.
    """
    from app.agent import run_turn
    from app.tools import ToolContext

    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=member_id,
                       sender_name=member_name, turn_mentions=[])
    async with _agent_lock:
        result = await run_turn(text, ctx, images=images)
    body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"
    attachments = render_bot_attachments(result)
    with db.session() as s:
        return post_message(s, room_id, None, body, attachments=attachments, kind="bot")
