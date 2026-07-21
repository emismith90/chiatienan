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

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import accounts, chat, drafts, ledger, roster, rooms
from app.auth import AuthCtx, require_admin, require_session
from app.bridge_smoke import run_bridge_smoke
from app.config import settings
from app.db import get_db
from app.images import sanitize_images
from app.models import Member, Room, RoomMessage
from app.money import MoneyError
from app.realtime import hub

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chiatienan")

app = FastAPI(title="chiatienan — PWA lunch bot")

# Strong refs to in-flight bot-turn tasks so they aren't GC'd mid-run.
_BG: set[asyncio.Task] = set()


class RoomIn(BaseModel):
    name: str = "Lunch"


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


class MessageIn(BaseModel):
    body: str
    images: list[dict] | None = None


class DraftPatchIn(BaseModel):
    payer_member_id: int | None = None
    member_participants: list[int] | None = None
    guests: list[str] | None = None
    bill_total: int | None = None
    adjustments: list[dict] | None = None
    dish: str | None = None
    initiator: str | None = None
    note: str | None = None
    status: str | None = None   # only "cancelled" is accepted


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
async def get_messages(room_id: int, since: int = 0, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    with get_db().session() as s:
        return {"messages": chat.list_messages(s, room_id, since_id=since)}


@app.post("/api/rooms/{room_id}/messages")
async def post_message(room_id: int, body: MessageIn, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    db = get_db()
    clean = sanitize_images(body.images)
    with db.session() as s:
        attachments = {"images": clean} if clean else None
        m = chat.post_message(s, room_id, ctx.member_id, body.body, attachments=attachments)
        payload = chat.message_to_dict(m, s.get(Member, ctx.member_id))
    await hub.publish(room_id, {"type": "message", **payload})

    if chat.is_clear_command(body.body):
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
                await hub.publish(room_id, {"type": "bot.done"})

        t = asyncio.create_task(_run_clear())
        _BG.add(t)
        t.add_done_callback(_BG.discard)
        return {"ok": True, "id": payload["id"]}

    if chat.mentions_bot(body.body):
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
                meal_msg = drafts.commit_draft(s, draft_id, room_id, logged_by=str(ctx.member_id))
            except (ledger.LedgerError, MoneyError) as e:
                raise HTTPException(409, str(e))
            meal_payload = chat.message_to_dict(meal_msg, None)
            draft_payload = chat.message_to_dict(s.get(RoomMessage, draft_id), None)
            meal_id = meal_msg.attachments["meal_id"]
    await hub.publish(room_id, {"type": "message", **draft_payload})
    await hub.publish(room_id, {"type": "message", **meal_payload})
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
    """Guarded Cursor SDK bridge validation (B3) — see app.bridge_smoke."""
    if not settings.admin_password or x_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="unauthorized")
    return await run_bridge_smoke()
