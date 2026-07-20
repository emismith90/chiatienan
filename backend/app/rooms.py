from __future__ import annotations
import secrets
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Room


def create_room(session: Session, name: str) -> Room:
    room = Room(name=name.strip() or "Lunch", invite_token=secrets.token_urlsafe(16))
    session.add(room); session.flush()
    return room


def room_by_invite(session: Session, invite_token: str) -> Room | None:
    return session.scalars(select(Room).where(Room.invite_token == invite_token)).first()


def room_by_id(session: Session, room_id: int) -> Room | None:
    return session.get(Room, room_id)
