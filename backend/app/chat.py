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
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import memory
from app.clock import now_ict
from app.config import settings
from app.db import Database
from app.models import Member, RoomMessage
from app.summarize import summarize_messages

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


_CLEAR_RE = re.compile(
    rf"^\s*(?:@(?:bot|{re.escape(settings.bot_handle)})\s+)?/clear\s*$",
    re.IGNORECASE,
)


def is_clear_command(text: str) -> bool:
    """True iff the whole message is the ``/clear`` command (optionally preceded
    by an ``@bot``/``@<handle>`` mention). Exact — ``/cleared``/``/clear now``
    do not match."""
    return _CLEAR_RE.match(text or "") is not None


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


def list_messages_page(session: Session, room_id: int, *, days: int | None = None,
                       before_id: int | None = None, limit: int = 100) -> tuple[list[dict], bool]:
    """A bounded window of messages (oldest→newest) plus whether older ones exist.

    Two access patterns drive the PWA's lazy scrollback:
      - Initial load — ``days=N``: only messages from the last N days (by
        ``created_at`` in ICT), so the client renders a small recent slice
        instead of the whole history.
      - Load earlier — ``before_id=X``: the page of ``limit`` messages with
        ``id < X`` (no time bound), for pulling in older history on demand.

    Either way the most-recent ``limit`` matching rows are returned, and
    ``has_more`` reports whether any message strictly older than the returned
    window still exists — so the client knows to keep offering "load earlier"
    (including when the recent window is empty but older history remains).
    """
    q = select(RoomMessage).where(RoomMessage.room_id == room_id)
    if before_id is not None:
        q = q.where(RoomMessage.id < before_id)
    if days is not None:
        q = q.where(RoomMessage.created_at >= now_ict() - timedelta(days=days))
    rows = list(reversed(
        session.scalars(q.order_by(RoomMessage.id.desc()).limit(limit)).all()
    ))

    # Anything older than the oldest returned row (or older than the requested
    # cursor when the window is empty) means there's more to load.
    floor = rows[0].id if rows else before_id
    older_q = select(RoomMessage.id).where(RoomMessage.room_id == room_id)
    if floor is not None:
        older_q = older_q.where(RoomMessage.id < floor)
    has_more = session.scalar(older_q.limit(1)) is not None

    authors = {m.id: m for m in session.scalars(select(Member).where(Member.room_id == room_id))}
    return [message_to_dict(r, authors.get(r.author_member_id)) for r in rows], has_more


def _render_messages(session: Session, room_id: int, rows, *, clamp: int = 500) -> str:
    """Render chat rows as ``«Name»: body`` / ``chiatienan: body`` lines,
    oldest→newest, each body clamped. Empty rows → ``""``."""
    if not rows:
        return ""
    authors = {a.id: a for a in session.scalars(select(Member).where(Member.room_id == room_id))}
    lines = []
    for r in rows:
        body = (r.body or "").strip()
        if len(body) > clamp:
            body = body[:clamp] + "…"
        if r.author_member_id is None:
            lines.append(f"chiatienan: {body}")
        else:
            author = authors.get(r.author_member_id)
            lines.append(f"«{author.display_name if author else '?'}»: {body}")
    return "\n".join(lines)


def build_history(session: Session, room_id: int, *, watermark: int = 0,
                  before_id: int | None = None, limit: int = 200) -> str:
    """Recent conversation fed to the agent: ``watermark < id [< before_id]``,
    text/bot kinds only, most-recent ``limit`` rows rendered oldest→newest."""
    q = select(RoomMessage).where(
        RoomMessage.room_id == room_id,
        RoomMessage.id > watermark,
        RoomMessage.kind.in_(("text", "bot")),
    )
    if before_id is not None:
        q = q.where(RoomMessage.id < before_id)
    rows = session.scalars(q.order_by(RoomMessage.id.desc()).limit(limit)).all()
    return _render_messages(session, room_id, list(reversed(rows)))


def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        if settle.get("type") == "settle_blocked":
            return dict(settle)
        return {"type": "settlement", **settle}
    statement = result.last_result("member_statement")
    if statement:
        return {"type": "statement", **statement}
    summary = result.last_result("get_period_summary")
    if summary:
        return {"type": "summary", **summary}
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


def _payment_body(attachments: dict) -> str:
    """Deterministic Vietnamese summary of recorded payment(s), from the tool/commit
    dict — never LLM prose (money-safety)."""
    transfers = attachments.get("transfers") or []
    if not transfers:
        return "💸 Đã ghi thanh toán."
    lines = [f"{t['from']['name']} trả {t['to']['name']} {t['amount']:,}đ" for t in transfers]
    return "💸 " + lines[0] if len(lines) == 1 else "💸 Đã ghi:\n" + "\n".join(lines)


def _settle_blocked_body(attachments: dict) -> str:
    """Deterministic Vietnamese summary of a blocked settle (pending drafts
    must be confirmed/cancelled first), straight from the tool-result dict —
    never from LLM prose (design D3, money-safety)."""
    lines = [attachments.get("message") or "Có đề xuất chưa xác nhận."]
    for p in attachments.get("pending") or []:
        if p.get("kind") == "payment":
            parts = ", ".join(
                f"{t['from_name']}→{t['to_name']} {t['amount']:,}đ" for t in (p.get("transfers") or [])
            )
            lines.append(f"• #{p['draft_id']}: {parts}")
        else:
            lines.append(
                f"• #{p['draft_id']}: {p.get('payer_name', '?')} trả "
                f"{p.get('bill_total', 0):,}đ ({p.get('participant_count', 0)} người)"
            )
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


def _statement_body(att: dict) -> str:
    """Deterministic VN text for a personal statement — numbers from the tool dict."""
    name = (att.get("member") or {}).get("name", "?")
    lines = [f"Số dư của {name}:"]
    owe = att.get("owe") or []
    owed = att.get("owed") or []
    if owe:
        lines.append("Bạn nợ:")
        lines += [f"• {r['name']} {r['amount']:,}đ ({r.get('dish') or 'bữa ăn'}"
                  f"{' – đã trả' if r['status'] == 'paid' else ''})" for r in owe]
    if owed:
        lines.append("Được nợ:")
        lines += [f"• {r['name']} {r['amount']:,}đ ({r.get('dish') or 'bữa ăn'})" for r in owed]
    if not owe and not owed:
        lines.append("Bạn đã cân bằng — không nợ ai, không ai nợ bạn.")
    else:
        lines.append(f"Ròng: {att.get('net', 0):,}đ")
    return "\n".join(lines)


def _summary_body(att: dict) -> str:
    """Deterministic VN text for the group summary — numbers from the tool dict."""
    period = att.get("period") or {}
    lines = [f"Tóm tắt đến {period.get('to')}:"]
    for e in att.get("timeline") or []:
        if e["kind"] == "meal":
            lines.append(f"• {e.get('occurred_on')} 🍜 {e.get('dish') or 'bữa ăn'} — "
                         f"{e.get('payer_name', '?')} trả {e.get('total', 0):,}đ")
        else:
            lines.append(f"• {e.get('occurred_on')} 💸 {e.get('from_name', '?')} → "
                         f"{e.get('to_name', '?')} {e.get('amount', 0):,}đ")
    if len(lines) == 1:
        lines.append("Chưa có giao dịch nào trong kỳ.")
    return "\n".join(lines)


async def run_bot_turn(db: Database, room_id: int, member_id: int, member_name: str,
                        text: str, images=None, emit=None,
                        before_id: int | None = None) -> RoomMessage:
    """Run the agent for one ``@bot`` turn and persist its reply.

    Serialized by ``_agent_lock`` so a ledger-writing tool call (``settle_period``)
    from concurrent turns never interleaves with another. Meal turns never write
    directly — ``propose_meal`` only proposes, and the turn ends with a pending
    ``expense_draft`` for a human to edit/commit. The draft write itself
    (``drafts.create_draft``, which persists the new draft independently — it
    never supersedes a prior pending draft — a ledger write) runs under the
    SAME lock as ``run_turn``, so the ledger's single-writer property covers
    this path too.

    ``emit`` — optional ``Callable[[dict], Awaitable[None]]`` — forwarded to
    :func:`app.agent.run_turn` for live ``agent.*`` progress.
    """
    from app import drafts
    from app.agent import run_turn
    from app.tools import ToolContext

    ctx = ToolContext(db=db, room_id=room_id, sender_member_id=member_id,
                       sender_name=member_name, turn_mentions=[])

    async with _agent_lock:
        await _maybe_rollover(db, room_id)
        mem_text = memory.load_memory(room_id)
        with db.session() as s:
            history = build_history(
                s, room_id, watermark=memory.read_watermark(room_id),
                before_id=before_id, limit=settings.history_max_messages,
            )
        result = await run_turn(text, ctx, images=images, emit=emit,
                                memory=mem_text or None, history=history or None)

        # A meal turn never writes directly: the LLM only proposes, and the
        # turn ends with an editable draft card for the human to confirm
        # (design D3, money-safety).
        proposal = result.last_result("propose_meal")
        # Collapse multiple proposals for the SAME (from,to) pair to the LAST
        # one (a model self-correction "100k… actually 150k"), preserving order.
        # Distinct pairs (real multi-payer) are untouched.
        _by_pair: dict[tuple[int, int], dict] = {}
        for p in result.all_results("propose_payment"):
            if p.get("type") == "payment_draft":
                _by_pair[(p["from_member_id"], p["to_member_id"])] = {
                    "from_member_id": p["from_member_id"], "to_member_id": p["to_member_id"],
                    "amount": p["amount"], "note": p.get("note")}
        payment_transfers = list(_by_pair.values())
        if proposal:
            payload = {k: proposal[k] for k in (
                "payer_member_id", "member_participants", "guests", "bill_total",
                "adjustments", "dish", "initiator", "note", "per_head_preview", "occurred_on")}
            payload["raw_input"] = text
            payload["logged_by"] = str(member_id)
            payload["turn_id"] = result.turn_id
            with db.session() as s:
                new_msg, _ = drafts.create_draft(s, room_id, payload)
        elif payment_transfers:
            payload = {"transfers": payment_transfers, "turn_id": result.turn_id}
            with db.session() as s:
                new_msg = drafts.create_payment_draft(s, room_id, payload)
        else:
            attachments = render_bot_attachments(result)

            # Money turns get a body built server-side from the tool-result
            # dict, so the visible text can never disagree with the
            # QR/attachment numbers (the LLM's `final_text` is never used for
            # the amounts themselves).
            if attachments and attachments.get("type") == "settlement":
                body = _settlement_body(attachments)
            elif attachments and attachments.get("type") == "settle_blocked":
                body = _settle_blocked_body(attachments)
            elif attachments and attachments.get("type") == "statement":
                body = _statement_body(attachments)
            elif attachments and attachments.get("type") == "summary":
                body = _summary_body(attachments)
            else:
                body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"

            with db.session() as s:
                new_msg = post_message(s, room_id, None, body, attachments=attachments, kind="bot")

            settle = result.last_result("settle_period")
            if emit and settle and settle.get("committed"):
                await emit({"type": "ledger:changed"})

    return new_msg


async def _maybe_rollover(db: Database, room_id: int) -> None:
    """Fold messages older than the recent window into ``memory.md`` and advance
    the watermark. No-op when nothing has aged out. Caller holds ``_agent_lock``."""
    cutoff = now_ict() - timedelta(weeks=settings.memory_window_weeks)
    with db.session() as s:
        wm = memory.read_watermark(room_id)
        aged = memory.messages_to_summarize(s, room_id, watermark=wm, older_than=cutoff)
        if not aged:
            return
        through_id = aged[-1].id
        rendered = _render_messages(s, room_id, aged)
    summary = await summarize_messages(rendered, kind="rollover")
    if summary:
        memory.append_summary(room_id, summary_text=summary, through_id=through_id,
                              through_at=now_ict().isoformat(), header="Tự động lưu (cũ hơn 10 tuần)")
    # On a blank/failed summary we leave the watermark untouched so the aged
    # messages are retried next turn — never silently dropped.


async def clear_context(db: Database, room_id: int, *, up_to_id: int, emit=None) -> RoomMessage:
    """Handle ``/clear``: summarize the live window into ``memory.md``, advance
    the watermark to ``up_to_id`` (the ``/clear`` line), and post a visible
    ``context_reset`` divider. Serialized by ``_agent_lock``."""
    async with _agent_lock:
        with db.session() as s:
            wm = memory.read_watermark(room_id)
            rows = memory.messages_to_summarize(s, room_id, watermark=wm, before_id=up_to_id)
            rendered = _render_messages(s, room_id, rows)
        summary = await summarize_messages(rendered, kind="clear") if rendered else ""
        now_iso = now_ict().isoformat()
        if summary:
            memory.append_summary(room_id, summary_text=summary, through_id=up_to_id,
                                  through_at=now_iso, header="Xoá ngữ cảnh")
        else:
            # No summary (empty window or summarizer failure) — still reset the
            # window; the user explicitly asked to clear.
            memory.set_watermark(room_id, through_id=up_to_id, through_at=now_iso)
        with db.session() as s:
            div = post_message(s, room_id, None,
                               "🧹 Đã lưu tóm tắt vào bộ nhớ; ngữ cảnh đã xoá.",
                               kind="context_reset")
    return div
