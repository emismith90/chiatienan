from __future__ import annotations
from dataclasses import dataclass
from fastapi import Header, HTTPException, Request
from app import accounts
from app.config import settings
from app.db import get_db


@dataclass
class AuthCtx:
    member_id: int
    room_id: int
    display_name: str
    nickname: str


def resolve_bearer(authorization: str | None) -> AuthCtx:
    raw = (authorization or "").strip()
    token = raw[7:].strip() if raw[:7].lower() == "bearer " else raw
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    with get_db().session() as s:
        m = accounts.member_for_token(s, token)
        if m is None:
            raise HTTPException(status_code=401, detail="invalid token")
        return AuthCtx(member_id=m.id, room_id=m.room_id, display_name=m.display_name, nickname=m.nickname)


async def require_session(request: Request) -> AuthCtx:
    return resolve_bearer(request.headers.get("Authorization"))


async def require_admin(x_admin_password: str | None = Header(default=None)) -> None:
    if not settings.admin_password or x_admin_password != settings.admin_password:
        raise HTTPException(status_code=401, detail="admin only")
