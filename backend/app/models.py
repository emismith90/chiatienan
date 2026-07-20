"""SQLAlchemy models — the append-only lunch ledger.

Invariants (design §4):
  * ``meals`` is immutable: corrections are a ``void`` + re-record, never an edit.
  * ``meal_shares`` sum to ``meals.total_amount`` exactly (enforced by
    :func:`app.money.split_shares` at write time).
  * ``settlements`` is an append-only event log of committed settle events; the
    default "since_last" window starts the day after the latest one.
  * ``processed_activities`` records each handled Teams ``activity.id`` so a
    re-delivered activity never writes a second meal.

All money is integer VND. Balances are derived from these rows, never stored.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.clock import now_ict


class Base(DeclarativeBase):
    pass


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    teams_user_id: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)
    aad_object_id: Mapped[str | None] = mapped_column(String(120), index=True)
    bank_code: Mapped[str | None] = mapped_column(String(40))
    account_number: Mapped[str | None] = mapped_column(String(40))
    account_holder: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)

    def has_bank_details(self) -> bool:
        return bool(self.bank_code and self.account_number and self.account_holder)


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payer_member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND
    note: Mapped[str | None] = mapped_column(String(400))
    raw_input: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="teams", nullable=False)  # teams|admin
    logged_by: Mapped[str | None] = mapped_column(String(120))  # teams_user_id
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
    period_from: Mapped[date | None] = mapped_column(Date)  # None = from ledger start
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)
    requested_by: Mapped[str | None] = mapped_column(String(120))  # teams_user_id
    transfers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # snapshot


class ProcessedActivity(Base):
    __tablename__ = "processed_activities"

    activity_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)
