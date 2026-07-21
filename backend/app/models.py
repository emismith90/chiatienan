"""SQLAlchemy models — the append-only lunch ledger.

Invariants (design §4):
  * ``meals`` is immutable: corrections are a ``void`` + re-record, never an edit.
  * ``meal_shares`` sum to ``meals.total_amount`` exactly (enforced by
    :func:`app.money.split_shares` at write time).
  * ``settlements`` is an append-only event log of committed settle events; the
    default "since_last" window starts the day after the latest one.

All money is integer VND. Balances are derived from these rows, never stored.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.clock import now_ict


class Base(DeclarativeBase):
    pass


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    invite_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("room_id", "nickname", name="uq_room_nickname"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    nickname: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    # NULL pin = "unclaimed" account (added by the agent, not yet claimed by a person).
    pin: Mapped[str | None] = mapped_column(String(20))  # identity handle, not a secret (D8)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    bank_code: Mapped[str | None] = mapped_column(String(40))
    account_number: Mapped[str | None] = mapped_column(String(40))
    account_holder: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)

    def has_bank_details(self) -> bool:
        return bool(self.bank_code and self.account_number and self.account_holder)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)


class RoomMessage(Base):
    __tablename__ = "room_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    author_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))  # None = bot
    kind: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text|bot|expense_draft
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attachments: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payer_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND
    note: Mapped[str | None] = mapped_column(String(400))
    raw_input: Mapped[str | None] = mapped_column(Text)
    dish: Mapped[str | None] = mapped_column(String(120))
    initiator: Mapped[str | None] = mapped_column(String(120))
    guests: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="web", nullable=False)  # web|admin
    logged_by: Mapped[str | None] = mapped_column(String(120))  # member id (str) of the logging session
    voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    voided_by: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)

    shares: Mapped[list["MealShare"]] = relationship(
        back_populates="meal", cascade="all, delete-orphan"
    )


class MealShare(Base):
    __tablename__ = "meal_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_id: Mapped[int] = mapped_column(ForeignKey("meals.id"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    share_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND

    meal: Mapped[Meal] = relationship(back_populates="shares")


class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    period_from: Mapped[date | None] = mapped_column(Date)  # None = from ledger start
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)
    requested_by: Mapped[str | None] = mapped_column(String(120))  # member id (str) who requested the settle
    transfers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # snapshot


class Payment(Base):
    """An ad-hoc cash payment between two members (outside meals/settlements).

    Adjusts balances directly (payer's balance += amount, payee's -= amount);
    carries no shares. Append-only; corrections are a void + new payment.
    """
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    from_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    to_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(String(400))
    source: Mapped[str] = mapped_column(String(20), default="web", nullable=False)
    logged_by: Mapped[str | None] = mapped_column(String(120))
    voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    voided_by: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)
