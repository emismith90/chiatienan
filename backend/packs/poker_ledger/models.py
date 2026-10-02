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


#: The pack's own journal (plan 2026-10-02, review R9): ``ledger_core`` knows nothing of
#: poker, so a game night and its void are events here, beside the ledger's.
GAMES = {
    "slug": "games", "name": "Poker games", "mode": "journal", "key": "", "searchable": [],
    "description": "Every poker game night of a room, append-only (plan 2026-10-02, release B).",
    "schema": {"type": "object", "required": ["event"],
               "properties": {"event": {"type": "string", "enum": ["game", "game_void"]},
                              "game_id": {"type": "integer"}}},
}

GAME_COLUMNS = ("id", "room_id", "played_on", "house", "note", "raw_input", "source", "logged_by",
                "voided", "voided_by", "voided_at", "created_at")


def _game_inserted(game) -> list[dict]:
    from ledger_core.journal import columns

    return [{"event": "game", "_order": game.id, **columns(game, GAME_COLUMNS),
             "entries": [{"member_id": e.member_id, "buy_in": e.buy_in, "cash_out": e.cash_out}
                         for e in sorted(game.entries, key=lambda e: e.id or 0)]}]


def _game_updated(game, kind: str) -> dict:
    from ledger_core.journal import dump_value

    return {"event": kind, "game_id": game.id, "voided": bool(game.voided),
            "voided_by": game.voided_by, "voided_at": dump_value(game.voided_at)}


def _entry_inserted(entry) -> list[dict]:
    from sqlalchemy.orm import object_session

    from ledger_core.journal import LedgerImmutable

    if entry.game is not None and entry.game in object_session(entry).new:
        return []
    raise LedgerImmutable(f"game #{entry.game_id}: entries are fixed when the game is recorded")


def register_mirrors() -> None:
    from ledger_core.journal import Mirror, register

    void = {"voided": "game_void", "voided_by": "game_void", "voided_at": "game_void"}
    register(Mirror(Game, "games", lambda r: r.room_id, _game_inserted, void, _game_updated))
    register(Mirror(GameEntry, "games", lambda r: r.game.room_id if r.game else 0, _entry_inserted, {}))


def bind(engine: Engine) -> None:
    """Create missing tables, add missing columns; never drops or retypes. Then the
    pack's journal and the mirror that keeps it in step."""
    from kernos.data import DataStore, ensure_internal
    from ledger_core.schema import session_factory

    Base.metadata.create_all(engine)
    sync_additive_columns(engine, Base.metadata)
    factory = session_factory(engine)
    ensure_internal(DataStore(factory), factory, [GAMES])
    register_mirrors()
