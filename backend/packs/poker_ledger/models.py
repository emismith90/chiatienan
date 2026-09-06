"""The poker business's own tables (design §7.2): a game night and its entries. On the
pack's own ``Base``, bound by ``bind(engine)`` with the same additive discipline as
the ledger; references into host tables (``room_id``, member ids) are plain integers."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from kernos.content.schema import sync_additive_columns
from ledger_core import clock


def _now() -> datetime:
    return clock.now()


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    played_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    house: Mapped[int] = mapped_column(Integer, default=0, nullable=False)      # rake / tips, VND
    note: Mapped[str | None] = mapped_column(String(400))
    raw_input: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="web", nullable=False)
    logged_by: Mapped[str | None] = mapped_column(String(120))
    voided: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    voided_by: Mapped[str | None] = mapped_column(String(120))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    entries: Mapped[list["GameEntry"]] = relationship(back_populates="game", cascade="all, delete-orphan")


class GameEntry(Base):
    __tablename__ = "game_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    member_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    buy_in: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_out: Mapped[int] = mapped_column(Integer, nullable=False)
    game: Mapped[Game] = relationship(back_populates="entries")


def bind(engine: Engine) -> None:
    """Create missing tables, add missing columns; never drops or retypes."""
    Base.metadata.create_all(engine)
    sync_additive_columns(engine, Base.metadata)
