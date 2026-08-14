"""FastAPI entrypoint — the PWA HTTP surface (design §5).

Routes: room create/join/identify (accounts), profile (``/api/me``), room chat
(list/post messages), the members roster, and the SSE stream. ``@bot`` mentions
dispatch a background agent turn; the SSE stream heartbeats every 25s and tells
slow clients to reconnect. Also keeps ``/health`` and the guarded
``/internal/bridge-smoke``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from logging.handlers import RotatingFileHandler

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import accounts, chat, debug_api, drafts, ledger, memos, roster, rooms
from app.auth import AuthCtx, require_admin, require_session
from app.clock import today_ict
from app.periods import resolve_period
from app.pi_smoke import run_bridge_smoke
from app.config import settings
from app.db import get_db
from app.images import sanitize_images
from app.models import Member, Room, RoomMessage
from app.money import MoneyError
from app.realtime import hub

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chiatienan")


def _mirror_logs_to_file() -> None:
    """Also write the stdout log to a file on the mounted volume.

    ``docker compose logs`` is held by the daemon and cannot be read from inside
    the container, so the debug export API has nothing to serve without this
    mirror. Rotates so it cannot fill the droplet's disk. Best-effort: if the
    directory is not writable we log and carry on — losing the mirror must never
    stop the app from booting.
    """
    if not settings.log_file:
        return
    try:
        os.makedirs(os.path.dirname(settings.log_file) or ".", exist_ok=True)
        handler = RotatingFileHandler(
            settings.log_file,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        # uvicorn installs its own handlers with propagate=False, so its
        # loggers bypass root entirely — without this, an unhandled exception
        # in a route (logged by uvicorn.error) would be in `docker compose
        # logs` but missing from the file the debug API serves, which is the
        # one traceback you always want.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            logging.getLogger(name).addHandler(handler)
    except OSError as exc:
        log.warning("could not open log mirror at %s: %s", settings.log_file, exc)


def _warn_if_workspace_is_ephemeral() -> None:
    """Say so at boot when the data directory won't survive a restart.

    ``/data`` is the only mounted volume in docker-compose.yml, so a directory
    outside it (production ran on ``/tmp/chiatienan-agent`` for weeks) loses every
    room's ``memory.md`` on each deploy — silently, because nothing errors. That
    was always this warning's real subject: with the sidecar there is no
    materialized workspace left to lose, only memory. Local runs point elsewhere on
    purpose, so this stays a warning and never a failure.
    """
    data_dir = settings.data_dir
    if not data_dir.startswith("/data"):
        log.warning(
            "DATA_DIR=%s is outside the mounted /data volume — every room's "
            "long-term memory will be lost on restart",
            data_dir,
        )


def _parse_iso_or_none(value: str | None):
    """``"2026-07-20"`` -> date; blank -> None; anything else raises ValueError."""
    from datetime import date as _date
    return _date.fromisoformat(value) if value else None


_mirror_logs_to_file()
_warn_if_workspace_is_ephemeral()

app = FastAPI(title="chiatienan — PWA lunch bot")
app.include_router(debug_api.router)


@app.on_event("shutdown")
async def _stop_sidecar() -> None:
    """Terminate the agent sidecar with the app.

    Without this the Node child outlives the event loop and asyncio complains at
    interpreter exit ("Event loop is closed"), and a restarted backend would leave
    an orphan holding a pipe nobody reads.
    """
    from app.pi_bridge import close_bridge

    await close_bridge()

# Strong refs to in-flight bot-turn tasks so they aren't GC'd mid-run.
_BG: set[asyncio.Task] = set()


class RoomIn(BaseModel):
    name: str = "Lunch"


class RoomCreateIn(BaseModel):
    room_name: str = "Lunch"
    display_name: str
    nickname: str
    pin: str
    bank_code: str | None = None
    account_number: str | None = None
    account_holder: str | None = None


class AccountIn(BaseModel):
    display_name: str
    nickname: str
    pin: str
    bank_code: str | None = None
    account_number: str | None = None
    account_holder: str | None = None


class IdentifyIn(BaseModel):
    nickname: str
    pin: str


class ProfileIn(BaseModel):
    display_name: str | None = None
    bank_code: str | None = None
    account_number: str | None = None
    account_holder: str | None = None
    default_participant: bool | None = None


class MessageIn(BaseModel):
    body: str
    images: list[dict] | None = None


class QuickPayIn(BaseModel):
    to: int
    meal_id: int


class QrRequestIn(BaseModel):
    to: int


class DraftPatchIn(BaseModel):
    payer_member_id: int | None = None
    member_participants: list[int] | None = None
    guests: list[str] | None = None
    bill_total: int | None = None
    adjustments: list[dict] | None = None
    items: list[dict] | None = None
    dish: str | None = None
    initiator: str | None = None
    note: str | None = None
    status: str | None = None   # only "cancelled" is accepted


class DraftEditIn(BaseModel):
    payer_member_id: int
    member_participants: list[int]
    guests: list[str] = []
    bill_total: int
    adjustments: list[dict] = []
    items: list[dict] = []
    dish: str | None = None
    initiator: str | None = None
    note: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/rooms")
async def create_room(body: RoomIn, _=Depends(require_admin)):
    with get_db().session() as s:
        r = rooms.create_room(s, body.name)
        out = {"room_id": r.id, "name": r.name, "invite_token": r.invite_token}
    if settings.caddy_domain:
        out["invite_link"] = f"https://{settings.caddy_domain}/join/{out['invite_token']}"
    return out


@app.post("/api/rooms/create")
async def create_room_public(body: RoomCreateIn):
    """Anyone can start a room: creates the room + its first member + a session.

    Public by design (multi-room spec 2026-07-22) — the invite-link join flow
    is the same trust model, and rooms are cheap. Member-field validation
    (nickname/PIN required, etc.) lives in accounts.create_account.
    """
    with get_db().session() as s:
        r = rooms.create_room(s, body.room_name)
        try:
            m, tok = accounts.create_account(
                s, r,
                display_name=body.display_name, nickname=body.nickname, pin=body.pin,
                bank_code=body.bank_code, account_number=body.account_number,
                account_holder=body.account_holder,
            )
        except accounts.AccountError as e:
            raise HTTPException(422, str(e))
        return {
            "token": tok, "room_id": r.id, "room_name": r.name,
            "member_id": m.id, "invite_token": r.invite_token,
        }


@app.get("/api/rooms/{invite_token}")
async def room_info(invite_token: str):
    with get_db().session() as s:
        r = rooms.room_by_invite(s, invite_token)
        if not r:
            raise HTTPException(404, "room not found")
        # Roster is public to invite-link holders so the join screen can show
        # who's here and which accounts are still unclaimed (pin is None) and
        # thus claimable on first sign-in. Never expose the pin or banking.
        members = [
            {"display_name": m.display_name, "nickname": m.nickname, "claimed": m.pin is not None}
            for m in roster.list_members(s, r.id)
        ]
        return {
            "room_id": r.id, "name": r.name, "bot_handle": settings.bot_handle,
            "members": members,
        }


@app.post("/api/rooms/{invite_token}/accounts")
async def join(invite_token: str, body: AccountIn):
    with get_db().session() as s:
        r = rooms.room_by_invite(s, invite_token)
        if not r:
            raise HTTPException(404, "room not found")
        try:
            m, tok = accounts.create_account(
                s, r,
                display_name=body.display_name, nickname=body.nickname, pin=body.pin,
                bank_code=body.bank_code, account_number=body.account_number,
                account_holder=body.account_holder,
            )
        except accounts.AccountError as e:
            raise HTTPException(409, str(e))
        return {"token": tok, "room_id": r.id, "member_id": m.id}


@app.post("/api/rooms/{invite_token}/identify")
async def identify(invite_token: str, body: IdentifyIn):
    with get_db().session() as s:
        r = rooms.room_by_invite(s, invite_token)
        if not r:
            raise HTTPException(404, "room not found")
        tok = accounts.identify(s, r, nickname=body.nickname, pin=body.pin)
        if not tok:
            raise HTTPException(401, "wrong nickname or PIN")
        return {"token": tok, "room_id": r.id}


@app.get("/api/me")
async def me(ctx: AuthCtx = Depends(require_session)):
    with get_db().session() as s:
        m = s.get(Member, ctx.member_id)
        return {
            "id": m.id, "display_name": m.display_name, "nickname": m.nickname,
            "bank_code": m.bank_code, "account_number": m.account_number,
            "account_holder": m.account_holder,
            "default_participant": m.default_participant,
        }


@app.put("/api/me")
async def update_me(body: ProfileIn, ctx: AuthCtx = Depends(require_session)):
    # exclude_unset so an explicit "" clears a bank field, while omitted keys
    # are left untouched.
    fields = body.model_dump(exclude_unset=True)
    with get_db().session() as s:
        accounts.update_profile(s, s.get(Member, ctx.member_id), **fields)
    return {"ok": True}


def _check_room(ctx: AuthCtx, room_id: int) -> None:
    if ctx.room_id != room_id:
        raise HTTPException(403, "wrong room")


@app.get("/api/rooms/{room_id}/members")
async def members(room_id: int, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    with get_db().session() as s:
        return [
            {
                "id": m.id,
                "display_name": m.display_name,
                "nickname": m.nickname,
                "claimed": m.pin is not None,
                "has_bank": m.has_bank_details(),
                # Bank details are shared within the room so members can transfer
                # to each other (the settlement QR already encodes them).
                "bank_code": m.bank_code,
                "account_number": m.account_number,
                "account_holder": m.account_holder,
                "default_participant": m.default_participant,
            }
            for m in roster.list_members(s, room_id)
        ]


@app.get("/api/rooms/{room_id}/invite")
async def room_invite(room_id: int, ctx: AuthCtx = Depends(require_session)):
    """Return the room's invite token so a member can share a join link.

    Any authenticated member of the room may fetch it — inviting others is the
    whole point. The client builds the URL from its own origin
    (``<origin>/join/<token>``) so it's correct in both dev and prod without
    depending on ``CADDY_DOMAIN``.
    """
    _check_room(ctx, room_id)
    with get_db().session() as s:
        r = s.get(Room, room_id)
        if not r:
            raise HTTPException(404, "room not found")
        return {"invite_token": r.invite_token}


@app.get("/api/rooms/{room_id}/messages")
async def get_messages(room_id: int, since: int = 0, days: int | None = None,
                       before_id: int | None = None,
                       ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    with get_db().session() as s:
        # Lazy scrollback: `days` (initial recent window) or `before_id` (older
        # page) return a bounded slice + `has_more`. Absent both, fall back to
        # the legacy since-based full list (SSE catch-up shape).
        if days is not None or before_id is not None:
            msgs, has_more = chat.list_messages_page(s, room_id, days=days, before_id=before_id)
            return {"messages": chat.annotate_settled_transfers(s, room_id, msgs),
                    "has_more": has_more}
        return {"messages": chat.annotate_settled_transfers(
            s, room_id, chat.list_messages(s, room_id, since_id=since))}


@app.get("/api/rooms/{room_id}/ledger")
async def get_ledger(room_id: int, period: str = "since_last",
                     from_: str | None = Query(None, alias="from"),
                     to: str | None = Query(None, alias="to"),
                     ctx: AuthCtx = Depends(require_session)):
    """The ledger for a period. ``from``/``to`` (ISO dates) scope it to an
    explicit range, so a "last week, day by day" question can open the panel on
    exactly the range that was asked about instead of the default window."""
    _check_room(ctx, room_id)
    try:
        explicit_from, explicit_to = _parse_iso_or_none(from_), _parse_iso_or_none(to)
    except ValueError:
        raise HTTPException(400, "from/to must be ISO dates (YYYY-MM-DD)") from None
    with get_db().session() as s:
        last = ledger.last_settlement(s, room_id)
        p = resolve_period(period if not (explicit_from or explicit_to) else "explicit",
                           today=today_ict(),
                           last_settlement_to=last.period_to if last else None,
                           explicit_from=explicit_from, explicit_to=explicit_to)
        outstanding = ledger.outstanding_pairs(s, room_id, p["from"], p["to"])
        timeline = ledger.period_timeline(s, room_id, p["from"], p["to"])
        me_stmt = ledger.statement_for(s, room_id, ctx.member_id, p["from"], p["to"])
        names = {mm.id: mm.display_name
                 for mm in roster.list_members(s, room_id, include_inactive=True)}
    for e in timeline:
        if e["kind"] == "meal":
            e["payer_name"] = names.get(e["payer_id"], "?")
        else:
            e["from_name"] = names.get(e["from_id"], "?")
            e["to_name"] = names.get(e["to_id"], "?")
    return {
        "period": {"from": p["from"].isoformat() if p["from"] else None,
                   "to": p["to"].isoformat(), "keyword": p["keyword"]},
        "outstanding": [{**r,
                         "debtor_name": names.get(r["debtor_id"], "?"),
                         "creditor_name": names.get(r["creditor_id"], "?")}
                        for r in outstanding],
        "timeline": timeline,
        "me": {
            "owe": [{**r, "name": names.get(r["other_id"], "?")} for r in me_stmt["owe"]],
            "owed": [{**r, "name": names.get(r["other_id"], "?")} for r in me_stmt["owed"]],
        },
    }


@app.post("/api/rooms/{room_id}/payments/quick")
async def quick_pay(room_id: int, body: QuickPayIn, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    async with chat._agent_lock:
        with db.session() as s:
            last = ledger.last_settlement(s, room_id)
            p = resolve_period("since_last", today=today_ict(),
                               last_settlement_to=last.period_to if last else None)
            edges = ledger.debt_breakdown(s, room_id, p["from"], p["to"])
            outstanding = sum(e.outstanding for e in edges
                              if e.debtor == ctx.member_id and e.creditor == body.to
                              and e.meal_id == body.meal_id)
            if outstanding <= 0:
                raise HTTPException(409, "nothing outstanding for that meal")
            pay = ledger.record_payment(s, room_id=room_id, from_member_id=ctx.member_id,
                                        to_member_id=body.to, amount=outstanding,
                                        meal_id=body.meal_id, logged_by=str(ctx.member_id))
            names = {mm.id: mm.display_name
                     for mm in roster.list_members(s, room_id, include_inactive=True)}
            # Same attachment shape as a confirmed payment draft, so this path
            # leaves the same audit trail instead of a bare line of text that
            # nothing can reconstruct later.
            pay_att = {
                "type": "payment",
                "transfers": [{
                    "from": {"id": ctx.member_id, "name": names.get(ctx.member_id, "?")},
                    "to": {"id": body.to, "name": names.get(body.to, "?")},
                    "amount": outstanding,
                }],
                "meal_id": body.meal_id,
            }
            msg = chat.post_message(s, room_id, None, chat._payment_body(pay_att),
                                    attachments=pay_att, kind="bot")
            msg_payload = chat.message_to_dict(msg, None)
    await hub.publish(room_id, {"type": "message", **msg_payload})
    await hub.publish(room_id, {"type": "ledger:changed"})
    return {"ok": True, "payment_id": pay["payment_id"], "amount": outstanding}


@app.post("/api/rooms/{room_id}/qr-requests")
async def qr_request(room_id: int, body: QrRequestIn, ctx: AuthCtx = Depends(require_session)):
    """The ledger QR button: post the VietQR for everything the caller owes one
    creditor, as a bot card in the chat.

    Fully deterministic — same `debt_breakdown` nets as a settlement, note from
    `build_qr_note`, no LLM turn (design D3). The card reuses the `settlement`
    attachment shape so `annotate_settled_transfers` retires the QR once paid.
    Reads only; posts a message but never records a payment.
    """
    from app.notes import build_qr_note
    from app.qr import QRError, make_qr_url

    _check_room(ctx, room_id)
    db = get_db()
    async with chat._agent_lock:
        with db.session() as s:
            last = ledger.last_settlement(s, room_id)
            p = resolve_period("since_last", today=today_ict(),
                               last_settlement_to=last.period_to if last else None)
            edges = [e for e in ledger.debt_breakdown(s, room_id, p["from"], p["to"])
                     if e.debtor == ctx.member_id and e.creditor == body.to
                     and e.outstanding > 0]
            amount = sum(e.outstanding for e in edges)
            if amount <= 0:
                raise HTTPException(409, "nothing outstanding for that member")

            members = {mm.id: mm for mm in roster.list_members(s, room_id, include_inactive=True)}
            payee, payer = members.get(body.to), members.get(ctx.member_id)
            if payee is None:
                raise HTTPException(404, "member not found")

            note = build_qr_note(
                payer.display_name if payer else "",
                [{"date": e.occurred_on, "dish": e.dish} for e in edges],
                fallback=f"Chia tien an {p['to'].day}/{p['to'].month}",
            )
            try:
                qr_url = make_qr_url(payee, amount, note)
            except QRError as exc:
                # Surface at the button — a card with no QR helps nobody.
                raise HTTPException(409, str(exc))

            att = {
                "type": "settlement",
                "period": {"from": p["from"].isoformat() if p["from"] else None,
                           "to": p["to"].isoformat()},
                "transfers": [{
                    "from_id": ctx.member_id,
                    "from_name": payer.display_name if payer else "?",
                    "to_id": body.to,
                    "to_name": payee.display_name,
                    "amount": amount,
                    "note": note,
                    "qr_url": qr_url,
                }],
                "requested": True,  # on-demand QR card, not a period settle
            }
            msg = chat.post_message(
                s, room_id, None,
                f"📱 QR chuyển khoản {payer.display_name if payer else '?'}"
                f" → {payee.display_name}: {amount:,}đ",
                attachments=att, kind="bot",
            )
            msg_payload = chat.message_to_dict(msg, None)
    await hub.publish(room_id, {"type": "message", **msg_payload})
    return {"ok": True, "amount": amount}


@app.post("/api/rooms/{room_id}/messages")
async def post_message(room_id: int, body: MessageIn, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    clean = sanitize_images(body.images)
    with db.session() as s:
        attachments = {"images": clean} if clean else None
        m = chat.post_message(s, room_id, ctx.member_id, body.body, attachments=attachments)
        payload = chat.message_to_dict(m, s.get(Member, ctx.member_id))
        # An answer to the bot's own question counts as addressed to it, mention
        # or not — "1" / "2" / "tôi đã trả tiền Emi" were all dropped in
        # production and retyped with @bot seconds later.
        answers_bot = not chat.mentions_bot(body.body) and chat.replies_to_bot_question(
            s, room_id, ctx.member_id, before_id=m.id,
        )
    await hub.publish(room_id, {"type": "message", **payload})

    if chat.is_clear_command(body.body):
        hub.mark_busy(room_id)
        await hub.publish(room_id, {"type": "bot.typing"})

        async def _run_clear():
            async def emit(ev):
                await hub.publish(room_id, ev)
            try:
                div = await chat.clear_context(db, room_id, up_to_id=payload["id"], emit=emit)
                await hub.publish(room_id, {"type": "message", **chat.message_to_dict(div, None)})
            except Exception:  # noqa: BLE001
                log.exception("clear_context failed in room %s", room_id)
            finally:
                hub.mark_idle(room_id)
                await hub.publish(room_id, {"type": "bot.done"})

        t = asyncio.create_task(_run_clear())
        _BG.add(t)
        t.add_done_callback(_BG.discard)
        return {"ok": True, "id": payload["id"]}

    if chat.mentions_bot(body.body) or answers_bot:
        hub.mark_busy(room_id)
        await hub.publish(room_id, {"type": "bot.typing"})

        async def _run():
            async def emit(ev):
                await hub.publish(room_id, ev)

            try:
                bot_msg = await chat.run_bot_turn(
                    db, room_id, ctx.member_id, ctx.display_name, body.body,
                    images=clean, emit=emit, before_id=payload["id"],
                )
                await hub.publish(room_id, {"type": "message", **chat.message_to_dict(bot_msg, None)})
            except Exception:  # noqa: BLE001 — never leave the room stuck
                log.exception("bot turn failed in room %s", room_id)
                try:
                    with db.session() as s:
                        err = chat.post_message(
                            s, room_id, None, "⚠️ The bot hit an error, please try again later.", kind="bot",
                        )
                        out = chat.message_to_dict(err, None)
                    await hub.publish(room_id, {"type": "message", **out})
                except Exception:  # noqa: BLE001
                    log.exception("failed to post bot error message in room %s", room_id)
            finally:
                hub.mark_idle(room_id)
                await hub.publish(room_id, {"type": "bot.done"})

        t = asyncio.create_task(_run())
        _BG.add(t)
        t.add_done_callback(_BG.discard)

    return {"ok": True, "id": payload["id"]}


@app.patch("/api/rooms/{room_id}/drafts/{draft_id}")
async def patch_draft(room_id: int, draft_id: int, body: DraftPatchIn,
                      ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    patch = body.model_dump(exclude_unset=True)
    if patch.get("status") not in (None, "cancelled"):
        raise HTTPException(400, "status only accepts 'cancelled'")
    db = get_db()
    with db.session() as s:
        try:
            m = drafts.update_draft(s, draft_id, room_id, patch)
        except (ledger.LedgerError, MoneyError) as e:
            raise HTTPException(404, str(e))
        payload = chat.message_to_dict(m, None)
    await hub.publish(room_id, {"type": "message", **payload})
    return {"ok": True}


@app.post("/api/rooms/{room_id}/drafts/{draft_id}/commit")
async def commit_draft_route(room_id: int, draft_id: int,
                             ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    async with chat._agent_lock:
        with db.session() as s:
            try:
                meal_msg = drafts.commit_any(s, draft_id, room_id, logged_by=str(ctx.member_id))
            except (ledger.LedgerError, MoneyError) as e:
                raise HTTPException(409, str(e))
            meal_payload = chat.message_to_dict(meal_msg, None)
            draft_payload = chat.message_to_dict(s.get(RoomMessage, draft_id), None)
            meal_id = (meal_msg.attachments or {}).get("meal_id")
    await hub.publish(room_id, {"type": "message", **draft_payload})
    await hub.publish(room_id, {"type": "message", **meal_payload})
    await hub.publish(room_id, {"type": "ledger:changed"})
    return {"ok": True, "meal_id": meal_id}


@app.post("/api/rooms/{room_id}/memos/{memo_id}/commit")
async def commit_memo_route(room_id: int, memo_id: int,
                            ctx: AuthCtx = Depends(require_session)):
    """Write a proposed lunch note to the room's observations file.

    Serialized under the same agent lock as the money drafts: `observations.md`
    is a read-modify-write file and the turn loop is the other writer.
    """
    _check_room(ctx, room_id)
    db = get_db()
    async with chat._agent_lock:
        with db.session() as s:
            try:
                m = memos.commit(s, memo_id, room_id)
            except memos.MemoError as e:
                raise HTTPException(404, str(e))
            payload = chat.message_to_dict(m, None)
    await hub.publish(room_id, {"type": "message", **payload})
    return {"ok": True}


@app.post("/api/rooms/{room_id}/memos/{memo_id}/cancel")
async def cancel_memo_route(room_id: int, memo_id: int,
                            ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    async with chat._agent_lock:
        with db.session() as s:
            try:
                m = memos.cancel(s, memo_id, room_id)
            except memos.MemoError as e:
                raise HTTPException(404, str(e))
            payload = chat.message_to_dict(m, None)
    await hub.publish(room_id, {"type": "message", **payload})
    return {"ok": True}


@app.post("/api/rooms/{room_id}/drafts/{draft_id}/recommit")
async def recommit_draft_route(room_id: int, draft_id: int, body: DraftEditIn,
                               ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    patch = body.model_dump(exclude_unset=True)
    async with chat._agent_lock:
        with db.session() as s:
            try:
                meal_msg = drafts.recommit_draft(s, draft_id, room_id, patch,
                                                 logged_by=str(ctx.member_id))
            except (ledger.LedgerError, MoneyError) as e:
                raise HTTPException(409, str(e))
            meal_payload = chat.message_to_dict(meal_msg, None)
            draft_payload = chat.message_to_dict(s.get(RoomMessage, draft_id), None)
            meal_id = meal_msg.attachments["meal_id"]
    await hub.publish(room_id, {"type": "message", **draft_payload})
    await hub.publish(room_id, {"type": "message", **meal_payload})
    await hub.publish(room_id, {"type": "ledger:changed"})
    return {"ok": True, "meal_id": meal_id}


@app.get("/api/rooms/{room_id}/stream")
async def stream(room_id: int, since: int = 0, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    q = hub.subscribe(room_id)

    async def gen():
        try:
            # Subscribed first, then catch-up is read: a message that lands
            # between subscribe() and this read can be delivered twice (once
            # via catch-up, once via the live queue). Clients must dedupe by
            # message `id`.
            with get_db().session() as s:  # catch-up
                for msg in chat.list_messages(s, room_id, since_id=since):
                    yield f"data: {json.dumps({'type': 'message', **msg})}\n\n"
            # Resync the ephemeral typing / "bot is working" indicator on every
            # (re)connect. A client that dropped the stream while a turn was
            # finishing would have missed the terminal bot.done / agent.run.*
            # events (they aren't persisted, so catch-up can't replay them) and
            # would otherwise show a stuck indicator forever. Re-asserting the
            # room's current busy state here self-heals that.
            resync = "bot.typing" if hub.is_busy(room_id) else "bot.done"
            yield f"data: {json.dumps({'type': resync})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event.get("type") == "__closed__":
                    return  # slow client dropped; it will reconnect with ?since=
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            hub.unsubscribe(room_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/internal/bridge-smoke")
async def bridge_smoke(x_admin_password: str | None = Header(default=None)):
    """Guarded sidecar liveness check — see app.pi_smoke."""
    if not settings.admin_password or x_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="unauthorized")
    return await run_bridge_smoke()
