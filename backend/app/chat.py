"""Room chat — persist/list messages, ``@bot`` detection, agent dispatch.

Human messages are appended via :func:`post_message` (called by the route
layer). A message that :func:`mentions_bot` triggers :func:`run_bot_turn`,
which serializes agent runs through a module-level ``asyncio.Lock`` (the
ledger has a single writer — design §3). A meal proposal ends the turn as a
pending ``kind="expense_draft"`` card (see :mod:`app.drafts`) instead of an
immediate reply; other turns post a ``kind="bot"`` message, with structured
tool results rendered via :func:`render_bot_attachments` rather than
re-parsed from LLM prose (design D3).
"""
from __future__ import annotations

import asyncio
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Database
from app.models import Member, RoomMessage

# In-process lock: only correct with a single uvicorn worker/process (see
# Dockerfile CMD). Multiple processes would each get their own lock and could
# interleave ledger-writing tool calls.
_agent_lock = asyncio.Lock()  # serialize agent runs (ledger single-writer)


def mentions_bot(text: str) -> bool:
    handle = re.escape(settings.bot_handle)
    # Negative lookbehind so an email/handle like `user@bot.com` doesn't count
    # as a mention — only a `@bot`/`@<handle>` preceded by a non-word, non-dot
    # boundary (e.g. start of string or whitespace) matches.
    return re.search(rf"(?<![\w.])@(bot|{handle})\b", text or "", re.IGNORECASE) is not None


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
    return None


def _settlement_body(attachments: dict) -> str:
    """Deterministic Vietnamese summary of a settlement, straight from the
    tool-result dict — never from LLM prose (design D3, money-safety)."""
    period = attachments.get("period") or {}
    p_from, p_to = period.get("from"), period.get("to")
    header = f"Chốt kỳ {p_from} → {p_to}:" if p_from else f"Chốt kỳ đến {p_to}:"

    transfers = attachments.get("transfers") or []
    lines = [header]
    if transfers:
        lines.extend(
            f"{t['from_name']} → {t['to_name']}: {t['amount']:,}đ" for t in transfers
        )
    else:
        lines.append(attachments.get("message") or "Không có gì để chốt.")

    for w in attachments.get("warnings") or []:
        lines.append(f"⚠️ {w}")
    return "\n".join(lines)


def _meal_body(attachments: dict) -> str:
    """Deterministic Vietnamese summary of a committed meal, straight from the
    tool-result dict — never from LLM prose (design D3, money-safety)."""
    payer = attachments.get("payer") or {}
    shares = attachments.get("shares") or []
    shares_str = ", ".join(f"{s['name']} {s['amount']:,}đ" for s in shares)
    bill = attachments.get("bill_total", attachments.get("tracked_total", attachments.get("total_amount", 0)))
    guests = attachments.get("guests") or []
    guest_str = f" (gồm {len(guests)} khách trả tiền mặt)" if guests else ""
    dish = attachments.get("dish")
    dish_str = f" — {dish}" if dish else ""
    return (
        f"Đã ghi #{attachments.get('meal_id')}{dish_str}: {payer.get('name', '?')} trả "
        f"tổng {bill:,}đ{guest_str} • {shares_str}"
    )


async def run_bot_turn(db: Database, room_id: int, member_id: int, member_name: str,
                        text: str, images=None, emit=None) -> RoomMessage:
    """Run the agent for one ``@bot`` turn and persist its reply.

    Serialized by ``_agent_lock`` so a ledger-writing tool call (``settle_period``)
    from concurrent turns never interleaves with another. Meal turns never write
    directly — ``propose_meal`` only proposes, and the turn ends with a pending
    ``expense_draft`` for a human to edit/commit.

    ``emit`` — optional ``Callable[[dict], Awaitable[None]]`` — forwarded to
    :func:`app.agent.run_turn` for live ``agent.*`` progress; unused otherwise.
    """
    from app.agent import run_turn
    from app.tools import ToolContext

    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=member_id,
                       sender_name=member_name, turn_mentions=[])
    async with _agent_lock:
        result = await run_turn(text, ctx, images=images, emit=emit)

    from app import drafts

    # A meal turn never writes directly: the LLM only proposes, and the turn
    # ends with an editable draft card for the human to confirm (design D3,
    # money-safety) — the draft may itself supersede/commit a prior pending
    # draft via drafts.create_draft.
    proposal = result.last_result("propose_meal")
    if proposal:
        payload = {k: proposal[k] for k in (
            "payer_member_id", "member_participants", "guests", "bill_total",
            "adjustments", "dish", "initiator", "note", "per_head_preview")}
        payload["raw_input"] = text
        payload["logged_by"] = str(member_id)
        with db.session() as s:
            return drafts.create_draft(s, room_id, payload)

    attachments = render_bot_attachments(result)

    # Money turns get a body built server-side from the tool-result dict, so
    # the visible text can never disagree with the QR/attachment numbers
    # (the LLM's `final_text` is never used for the amounts themselves).
    if attachments and attachments.get("type") == "settlement":
        body = _settlement_body(attachments)
    else:
        body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"

    with db.session() as s:
        return post_message(s, room_id, None, body, attachments=attachments, kind="bot")
