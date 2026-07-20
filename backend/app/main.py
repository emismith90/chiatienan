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

from app import accounts, chat, roster, rooms
from app.auth import AuthCtx, require_admin, require_session
from app.bridge_smoke import run_bridge_smoke
from app.config import settings
from app.db import get_db
from app.images import sanitize_images
from app.models import Member
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
        return {"room_id": r.id, "name": r.name}


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
            raise HTTPException(401, "sai biệt danh hoặc PIN")
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
            {"id": m.id, "display_name": m.display_name, "nickname": m.nickname}
            for m in roster.list_members(s, room_id)
        ]


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

    if chat.mentions_bot(body.body):
        await hub.publish(room_id, {"type": "bot.typing"})

        async def _run():
            try:
                bot_msg = await chat.run_bot_turn(
                    db, room_id, ctx.member_id, ctx.display_name, body.body, images=clean,
                )
                await hub.publish(room_id, {"type": "message", **chat.message_to_dict(bot_msg, None)})
            except Exception:  # noqa: BLE001 — never leave the room stuck
                log.exception("bot turn failed in room %s", room_id)
                try:
                    with db.session() as s:
                        err = chat.post_message(
                            s, room_id, None, "⚠️ Bot gặp lỗi, thử lại sau.", kind="bot",
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


@app.get("/api/rooms/{room_id}/stream")
async def stream(room_id: int, since: int = 0, ctx: AuthCtx = Depends(require_session)):
    _check_room(ctx, room_id)
    q = hub.subscribe(room_id)

    async def gen():
        try:
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
