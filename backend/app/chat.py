"""Room chat — persist/list messages, ``@phoenix`` detection, agent dispatch.

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
import logging
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import memory, moneyguard
from app.clock import now_ict
from app.config import settings
from app.db import Database
from app.models import Meal, Member, RoomMessage
from app.summarize import summarize_messages

log = logging.getLogger("chiatienan")

# In-process lock: only correct with a single uvicorn worker/process (see
# Dockerfile CMD). Multiple processes would each get their own lock and could
# interleave ledger-writing tool calls.
_agent_lock = asyncio.Lock()  # serialize agent runs (ledger single-writer)


def mentions_bot(text: str) -> bool:
    handle = re.escape(settings.bot_handle)
    # Negative lookbehind so an email/handle like `user@bot.com` doesn't count
    # as a mention — only a `@phoenix`/`@<handle>` preceded by a non-word,
    # non-dot boundary (e.g. start of string or whitespace) matches. Both names
    # are hardcoded alongside the configured handle: `@bot` is the pre-rebrand
    # legacy alias (months of muscle memory), and `@phoenix` must keep working
    # even on a deployment whose .env still pins BOT_HANDLE=bot.
    return re.search(rf"(?<![\w.])@(bot|phoenix|{handle})\b", text or "", re.IGNORECASE) is not None


_CLEAR_RE = re.compile(
    rf"^\s*(?:@(?:bot|phoenix|{re.escape(settings.bot_handle)})\s+)?/clear\s*$",
    re.IGNORECASE,
)


def is_clear_command(text: str) -> bool:
    """True iff the whole message is the ``/clear`` command (optionally preceded
    by an ``@phoenix``/``@<handle>`` mention). Exact — ``/cleared``/``/clear now``
    do not match."""
    return _CLEAR_RE.match(text or "") is not None


#: How long a bot question stays "open" for a bare reply.
_REPLY_WINDOW_MINUTES = 10

#: A bot message that is waiting on an answer: it asked something, or offered a
#: numbered choice ("1. Trả trọn … 2. Cấn trừ …").
_CHOICE_RE = re.compile(r"(?:^|\n)\s*(?:\*\*)?[1-9][.)]\s", re.MULTILINE)


def _awaits_an_answer(body: str) -> bool:
    text = (body or "").strip()
    return text.endswith("?") or bool(_CHOICE_RE.search(text))


def replies_to_bot_question(session: Session, room_id: int, member_id: int,
                            *, before_id: int) -> bool:
    """True when a message with no ``@phoenix`` is plainly answering the bot.

    People answer a question the way they would answer a person — "1", "2", "b",
    "tôi đã trả tiền Emi" — and every one of those was dropped in production for
    lacking a mention, then retyped with one seconds later (four times in one
    conversation).

    Deliberately narrow, because the cost of a false positive is the bot barging
    into a human conversation. ALL of:

    * the message immediately before this one is the bot's,
    * that message asked something (``?``) or offered a numbered choice,
    * it is less than :data:`_REPLY_WINDOW_MINUTES` old,
    * and *this* sender is the one whose message triggered that bot turn.
    """
    previous = session.scalars(
        select(RoomMessage)
        .where(RoomMessage.room_id == room_id, RoomMessage.id < before_id)
        .order_by(RoomMessage.id.desc())
        .limit(1)
    ).first()
    if previous is None or previous.author_member_id is not None or previous.kind != "bot":
        return False
    if not _awaits_an_answer(previous.body):
        return False
    if previous.created_at is not None and previous.created_at < (
        now_ict().replace(tzinfo=None) - timedelta(minutes=_REPLY_WINDOW_MINUTES)
    ):
        return False
    asker = session.scalars(
        select(RoomMessage)
        .where(
            RoomMessage.room_id == room_id,
            RoomMessage.id < previous.id,
            RoomMessage.author_member_id.is_not(None),
        )
        .order_by(RoomMessage.id.desc())
        .limit(1)
    ).first()
    return asker is not None and asker.author_member_id == member_id


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


def annotate_settled_transfers(session: Session, room_id: int, payloads: list[dict]) -> list[dict]:
    """Mark transfers on old settlement cards that are no longer owed.

    A settlement card carries a live VietQR, and it stays in the thread forever.
    Once the debt is paid the QR is still scannable — production history holds 34
    such links, two of which are for a transfer that was never owed at all (the
    windowing bug, since fixed). Scroll back far enough, tap one, and you pay
    money you do not owe.

    Annotating at read time rather than storing it means the whole backlog is
    corrected at once and stays correct as people pay. The stored attachment is
    never modified — the room's history stays exactly what was said at the time,
    which is also why the amount is left alone and only ``settled`` is added.
    """
    def _is_settlement(payload: dict) -> bool:
        att = payload.get("attachments")
        return isinstance(att, dict) and att.get("type") == "settlement"

    if not any(_is_settlement(p) for p in payloads):
        return payloads

    from app import ledger  # lazy: ledger imports chat for post_message

    outstanding: dict[tuple[int, int], int] = {}
    for t in ledger.period_transfers(session, room_id, None, now_ict().date()):
        outstanding[(t.from_member, t.to_member)] = t.amount

    for payload in payloads:
        if not _is_settlement(payload):
            continue
        att = payload["attachments"]
        # Rebuild rather than assign into it: message_to_dict hands back the ORM
        # object's JSON by reference, so annotating in place would write `settled`
        # into the stored row on the next flush and rewrite the room's history.
        payload["attachments"] = {
            **att,
            "transfers": [
                {**t, "settled": t.get("amount", 0)
                 > outstanding.get((t.get("from_id"), t.get("to_id")), 0)}
                for t in (att.get("transfers") or [])
            ],
        }
    return payloads


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
    """Render chat rows as ``«Name»: body`` / ``phoenix: body`` lines,
    oldest→newest, each body clamped. Empty rows → ``""``.

    An image attachment is rendered as a ``[ảnh: N]`` marker. The bytes cannot
    go into the text history, but without the marker a bill someone pasted a
    message ago is completely invisible to the model — which is how it ends up
    asking for a total that is sitting right there in the room.
    """
    if not rows:
        return ""
    authors = {a.id: a for a in session.scalars(select(Member).where(Member.room_id == room_id))}
    lines = []
    for r in rows:
        body = (r.body or "").strip()
        if len(body) > clamp:
            body = body[:clamp] + "…"
        n_images = len(((r.attachments or {}).get("images")) or [])
        if n_images:
            body = (f"{body} " if body else "") + f"[ảnh: {n_images}]"
        if r.author_member_id is None:
            # The label must match the persona name in prompt.py so the model
            # recognises its own past replies in the history it is handed.
            lines.append(f"phoenix: {body}")
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


def recent_images(session: Session, room_id: int, *, before_id: int | None = None,
                  max_messages: int | None = None, max_minutes: int | None = None) -> list[dict]:
    """Images from the most recent image-bearing message in the room's live window.

    People paste the bill and *then* say "@phoenix log đi" — two messages. The turn's
    own attachments are empty, so without this the bot never sees the bill and
    asks for a total that is already on screen. Only the newest image-bearing
    message is carried forward (one bill, not a scrollback of them), and only
    while it is plausibly the bill under discussion: within the last
    ``max_messages`` messages and ``max_minutes`` minutes.
    """
    max_messages = settings.image_lookback_messages if max_messages is None else max_messages
    max_minutes = settings.image_lookback_minutes if max_minutes is None else max_minutes
    if max_messages <= 0:
        return []
    q = select(RoomMessage).where(
        RoomMessage.room_id == room_id,
        # Bound in SQL, like list_messages_page: SQLite hands back naive
        # datetimes, so an in-Python compare against aware now_ict() would raise.
        RoomMessage.created_at >= now_ict() - timedelta(minutes=max_minutes),
    )
    if before_id is not None:
        q = q.where(RoomMessage.id < before_id)
    rows = session.scalars(q.order_by(RoomMessage.id.desc()).limit(max_messages)).all()
    for r in rows:
        images = ((r.attachments or {}).get("images")) or []
        if images:
            return [img for img in images if isinstance(img, dict) and img.get("data")]
    return []


def render_bot_attachments(result) -> dict | None:
    settle = result.last_result("settle_period")
    if settle:
        if settle.get("type") == "settle_blocked":
            return dict(settle)
        return {"type": "settlement", **settle}
    statement = result.last_result("member_statement")
    if statement and statement.get("type") == "statement":
        return {"type": "statement", **statement}
    summary = result.last_result("get_period_summary")
    if summary and summary.get("type") == "summary":
        return {"type": "summary", **summary}
    pick = result.last_result("pick_random")
    if pick and pick.get("type") == "random_pick":
        return {"type": "random_pick", **pick}
    return None


def _settlement_body(attachments: dict) -> str:
    """Deterministic Vietnamese summary of a settlement, straight from the
    tool-result dict — never from LLM prose (design D3, money-safety)."""
    period = attachments.get("period") or {}
    p_from, p_to = period.get("from"), period.get("to")
    # "Tạm tính", not "Chốt kỳ": nothing is recorded and no period closes, so a
    # header that reads like a closing entry was telling the room the books had
    # been ruled off when `settlements` had been empty since the ledger began.
    header = f"Tạm tính {p_from} → {p_to}:" if p_from else f"Tạm tính đến {p_to}:"

    transfers = attachments.get("transfers") or []
    lines = [header]
    if transfers:
        # The memo rides along the QR as the bank's addInfo, and it is the part
        # people dispute ("sai nội dung chuyển khoản r"). It was only ever in the
        # attachment, so nobody could see what it said without opening the card.
        lines.extend(
            f"{t['from_name']} → {t['to_name']}: {t['amount']:,}đ"
            + (f" · ND: {t['note']}" if t.get("note") else "")
            for t in transfers
        )
    else:
        lines.append(attachments.get("message") or "Không ai nợ ai.")

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
    # Production: this listed the blocking draft and stopped there, so people
    # asked the bot to close it four different ways instead of scrolling up to
    # the card. Say where the buttons are, and that chat can cancel it.
    if attachments.get("pending"):
        lines.append(
            "Mở thẻ nháp ở trên (theo số #) rồi bấm **Xác nhận** hoặc **Huỷ** — "
            'hoặc nhắn "huỷ đề xuất #<số>" là mình huỷ hộ.'
        )
    return "\n".join(lines)


def _meal_exists(db: Database, room_id: int, meal_id: int) -> bool:
    """Is ``meal_id`` a live (non-voided) meal of ``room_id``?

    Room-scoped and void-aware on purpose: "Đã ghi #14" is a claim about *this*
    room's ledger, and a voided meal is one the room decided never happened.
    """
    with db.session() as s:
        meal = s.get(Meal, meal_id)
        return meal is not None and meal.room_id == room_id and not meal.voided


#: What the room sees instead of a forged confirmation. It has to say the thing
#: the forgery hid — that the ledger is untouched — because the failure is
#: invisible otherwise: nothing was written, so no balance moves and no card
#: appears, and the next person to read the thread has only the bot's word.
_FABRICATED_COMMIT_BODY = (
    "⚠️ Mình **chưa ghi** bữa này vào sổ — chưa có gì thay đổi cả.\n"
    "Nhắn lại giúp mình nhé (kèm ảnh hoá đơn nếu có, và nói rõ ai trả / chia cho ai) — "
    "chỉ khi thẻ nháp hiện ra và có người bấm **Xác nhận** thì mới thật sự vào sổ."
)


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
    """Deterministic VN text for a personal statement — numbers from the tool dict.

    Two sections and no total. The old closing line, "Ròng: -54.500đ", was the
    one number in the reply nobody could act on: it is not what you send anyone,
    it is not what anyone sends you, and when the two sections disagreed with it
    (a debt in each direction) it read as though they had been cancelled out.
    What you owe and what you are owed, per person per meal, is the whole answer.
    """
    name = (att.get("member") or {}).get("name", "?")
    lines = [f"{name} — nợ và được nợ:"]
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
        lines.append("Bạn không nợ ai, không ai nợ bạn.")
    return "\n".join(lines)


def _summary_body(att: dict) -> str:
    """One-line headline for a group summary — numbers from the tool dict.

    This used to print every row. Fifteen transactions arrived as one unbroken
    paragraph, and when someone asked for it "day by day, as bullet points" they
    got the identical blob back, because the body is rendered server-side and
    cannot honour a formatting request. The detail belongs in the card below it
    (grouped by day) and in the ledger panel the card can open — a chat bubble is
    the wrong surface for a table.
    """
    period = att.get("period") or {}
    timeline = att.get("timeline") or []
    meals = sum(1 for e in timeline if e["kind"] == "meal")
    payments = len(timeline) - meals
    window = (f"{period.get('from')} → {period.get('to')}" if period.get("from")
              else f"đến {period.get('to')}")
    if not timeline:
        return f"Tóm tắt {window}: chưa có giao dịch nào trong kỳ."
    parts = []
    if meals:
        parts.append(f"{meals} bữa")
    if payments:
        parts.append(f"{payments} lượt trả tiền")
    days = len({e.get("occurred_on") for e in timeline})
    return f"Tóm tắt {window}: {', '.join(parts)} trong {days} ngày — chi tiết ở dưới."


def _random_pick_body(att: dict) -> str:
    """Deterministic VN text for a random draw — the winner comes from the tool
    dict, never the LLM, so it can't be re-typed into a different name."""
    chosen = att.get("chosen") or {}
    n = len(att.get("candidates") or [])
    label = att.get("label")
    tail = f" ({label})" if label else ""
    return f"🎲 Người được chọn{tail}: **{chosen.get('name', '?')}** — bốc trong {n} người."


async def run_bot_turn(db: Database, room_id: int, member_id: int, member_name: str,
                        text: str, images=None, emit=None,
                        before_id: int | None = None) -> RoomMessage:
    """Run the agent for one ``@phoenix`` turn and persist its reply.

    Serialized by ``_agent_lock`` so a ledger-writing tool call (``settle_period``)
    from concurrent turns never interleaves with another. Meal turns never write
    directly — ``propose_meal`` only proposes, and the turn ends with a pending
    ``expense_draft`` for a human to edit/commit. The draft write itself
    (``drafts.create_draft``, which persists the new draft and retires any
    pending draft it re-proposes, without recording anything) runs under the
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
            # "bill pasted, then @phoenix in the next message" is the normal way
            # people use this — carry the recent bill into the turn.
            if not images:
                images = recent_images(s, room_id, before_id=before_id) or None
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
        # Cards this turn retired by re-proposing them, published below so their
        # Confirm/Cancel buttons disappear everywhere instead of lingering as a
        # pending draft that blocks the next settle.
        superseded_payloads: list[dict] = []
        if proposal:
            payload = {k: proposal.get(k) for k in (
                "payer_member_id", "member_participants", "guests", "bill_total",
                "adjustments", "items", "discount_split", "dish", "initiator", "note",
                "per_head_preview", "occurred_on")}
            payload["raw_input"] = text
            payload["logged_by"] = str(member_id)
            payload["turn_id"] = result.turn_id
            with db.session() as s:
                new_msg, superseded = drafts.create_draft(s, room_id, payload)
                superseded_payloads = [message_to_dict(m, None) for m in superseded]
        elif payment_transfers:
            payload = {"transfers": payment_transfers, "turn_id": result.turn_id}
            with db.session() as s:
                new_msg, superseded = drafts.create_payment_draft(s, room_id, payload)
                superseded_payloads = [message_to_dict(m, None) for m in superseded]
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
            elif attachments and attachments.get("type") == "random_pick":
                body = _random_pick_body(attachments)
            else:
                body = result.final_text or (result.error and f"⚠️ {result.error}") or "(không có phản hồi)"
                # The one path where money reaches the room as LLM prose. Report
                # it (see app.moneyguard); enforcing comes after the log is quiet.
                if result.final_text:
                    # …except for the one class that is wrong however the numbers
                    # were obtained: prose that says the ledger was written when
                    # no tool wrote it. 2026-08-14, room 3 — a bill photo and
                    # "log this for all" came back in 6.1s with `tools=0` as a
                    # word-perfect forgery of `_meal_body`: "Đã ghi #14 — Texas
                    # Chicken: Bạch Mai trả tổng 793,760đ • …". There was no meal
                    # #14, "Bạch Mai" is the branch on the receipt rather than
                    # anyone in the room, and the split listed six of seven
                    # members. Nothing distinguished it from a real confirmation,
                    # so the room believed it, and asking again just reproduced
                    # it. Reporting is not enough for this one: the message must
                    # not be posted.
                    #
                    # `meal_exists` is what makes that stick across repeats. The
                    # forgery was posted, so its numbers joined the room's own
                    # history, and the history legitimately backs amounts — which
                    # quietly cleared every retelling (three more over 44
                    # minutes, `tools=0` each time). The ledger cannot be talked
                    # round: "Đã ghi #14" is checked against `meals`.
                    forged = moneyguard.fabricated_commit(
                        body, f"{text}\n{history or ''}", result.tools,
                        meal_exists=lambda mid: _meal_exists(db, room_id, mid),
                    )
                    if forged is not None:
                        log.error(
                            "suppressed fabricated commit: room=%s turn=%s amounts=%s "
                            "images=%d tools=%s text=%r",
                            room_id, result.turn_id, forged, len(images or []),
                            [inv.name for inv in result.tools], body[:400],
                        )
                        body = _FABRICATED_COMMIT_BODY
                    else:
                        # The history counts as the user having said it. A number the
                        # room stated two messages ago and the bot repeats back
                        # ("bạn nói tổng 324k") is not invented money, and flagging it
                        # buries the alerts that matter — the benchmark's `p102`/`p104`
                        # replies were reported as unbacked for quoting a total from the
                        # conversation they were handed.
                        stray = moneyguard.unbacked_amounts(
                            body, f"{text}\n{history or ''}", result.tools)
                        if stray:
                            # images=N matters for triage. Replaying four days of
                            # production through this: of the alerts that survive a
                            # tool-output allow-set, all but one were prices the model
                            # read off a bill photo — correct, but unattributable by
                            # construction because image content is not text. Those
                            # stay a warning; the forgeries above are what blocks.
                            log.warning(
                                "unbacked money in reply: room=%s turn=%s amounts=%s images=%d tools=%s",
                                room_id, result.turn_id, stray, len(images or []),
                                [inv.name for inv in result.tools],
                            )

            with db.session() as s:
                new_msg = post_message(s, room_id, None, body, attachments=attachments, kind="bot")

            # No ledger:changed here any more: settle_period is read-only, so a
            # settlement cannot alter a balance. Meal and payment commits emit it
            # from their own routes.

        # A draft the bot cancelled on request is the same situation as a
        # superseded one: open clients still show its buttons until the card
        # itself is republished.
        cancelled_ids = [r.get("draft_id") for r in result.all_results("cancel_draft")]
        if cancelled_ids:
            with db.session() as s:
                for draft_id in cancelled_ids:
                    card = s.get(RoomMessage, draft_id) if draft_id else None
                    if card is not None and card.room_id == room_id:
                        superseded_payloads.append(message_to_dict(card, None))

    if emit:
        for stale in superseded_payloads:
            await emit({"type": "message", **stale})

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
