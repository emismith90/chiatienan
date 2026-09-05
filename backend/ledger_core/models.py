"""The ledger's own tables (plan Task 3.2, decision 2).

Same table and column names as before the extraction, so an existing database
is unchanged. References into host tables — ``room_id``, member ids, ``place_id``
— are plain indexed integers: SQLAlchemy cannot declare a foreign key across
declarative bases, and a second host may not have ``rooms`` or ``members`` at all.
Existing production tables keep the constraints they were created with; a fresh
install has none here, and the ledger validates members in code instead
(:mod:`ledger_core.members`).

Invariants (design §4): ``meals`` is immutable — corrections are a ``void`` +
re-record; ``meal_shares`` sum to ``meals.total_amount`` exactly; ``settlements``
is an append-only event log; balances are derived, never stored.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ledger_core import clock


def _now() -> datetime:
    return clock.now()


class Base(DeclarativeBase):
    pass


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payer_member_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND
    note: Mapped[str | None] = mapped_column(String(400))
    raw_input: Mapped[str | None] = mapped_column(Text)
    dish: Mapped[str | None] = mapped_column(String(120))
    place_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    initiator: Mapped[str | None] = mapped_column(String(120))
    guests: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="web", nullable=False)  # web|admin
    logged_by: Mapped[str | None] = mapped_column(String(120))  # member id (str) of the logging session
    voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    voided_by: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    shares: Mapped[list["MealShare"]] = relationship(
        back_populates="meal", cascade="all, delete-orphan"
    )


class MealShare(Base):
    __tablename__ = "meal_shares"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_id: Mapped[int] = mapped_column(ForeignKey("meals.id"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    share_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND

    meal: Mapped[Meal] = relationship(back_populates="shares")


class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    period_from: Mapped[date | None] = mapped_column(Date)  # None = from ledger start
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    requested_by: Mapped[str | None] = mapped_column(String(120))  # member id (str) who requested the settle
    transfers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # snapshot


class Payment(Base):
    """An ad-hoc cash payment between two members (outside meals/settlements).

    Adjusts balances directly (payer's balance += amount, payee's -= amount);
    carries no shares. Append-only; corrections are a void + new payment.
    ``meal_id`` links a payment to the thing it settles; ``ref_kind`` (additive,
    default ``"meal"``) says what kind of thing that is, so a second business can
    link payments to its own records without a new column.
    """
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    from_member_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    to_member_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # VND
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meal_id: Mapped[int | None] = mapped_column(ForeignKey("meals.id"), nullable=True, default=None)
    ref_kind: Mapped[str] = mapped_column(String(20), default="meal", server_default=text("'meal'"), nullable=False)
    note: Mapped[str | None] = mapped_column(String(400))
    source: Mapped[str] = mapped_column(String(20), default="web", nullable=False)
    logged_by: Mapped[str | None] = mapped_column(String(120))
    voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    voided_by: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
