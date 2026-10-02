"""Import the poker tables into the pack's ``games`` journal once, and check the two
agree — the poker half of :mod:`ledger_core.importer` (plan 2026-10-02, B5)."""
from __future__ import annotations

from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledger_core import journal
from packs.poker_ledger.models import Game, _game_inserted
from packs.poker_ledger.view import EntryRecord, GameRecord, games


def game_rooms(session: Session) -> list[int]:
    return sorted(set(session.scalars(select(Game.room_id).distinct()).all()))


def import_room(session: Session, room_id: int) -> int:
    rows = sorted(session.scalars(select(Game).where(Game.room_id == room_id)),
                  key=lambda g: (g.created_at is None, g.created_at or 0, g.id))
    for g in rows:
        ev = _game_inserted(g)[0]
        ev.pop("_order", None)
        journal.append_raw(session, "games", room_id, ev)
    return len(rows)


def differences(session: Session, room_id: int) -> list[str]:
    return _compare(session, room_id)[0]


def reconcile(session: Session, room_id: int) -> int:
    """:func:`ledger_core.importer.reconcile`, for games."""
    _, stale = _compare(session, room_id)
    for gid in sorted(stale):
        ev = _game_inserted(session.get(Game, gid))[0]
        ev.pop("_order", None)
        journal.append_raw(session, "games", room_id, {**ev, "reconciled": True})
    return len(stale)


def _compare(session: Session, room_id: int) -> tuple[list[str], set[int]]:
    table = {g.id: GameRecord(
        id=g.id, room_id=g.room_id, played_on=g.played_on, house=g.house, note=g.note, raw_input=g.raw_input,
        source=g.source, logged_by=g.logged_by, voided=bool(g.voided), voided_by=g.voided_by,
        voided_at=g.voided_at.replace(tzinfo=None) if g.voided_at else None,
        created_at=g.created_at.replace(tzinfo=None) if g.created_at else None,
        entries=tuple(EntryRecord(g.id, e.member_id, e.buy_in, e.cash_out) for e in sorted(g.entries, key=lambda e: e.id)))
        for g in session.scalars(select(Game).where(Game.room_id == room_id))}
    jour = {g.id: g for g in games(session, room_id, voided=None)}
    out, stale = [], set()
    for gid in sorted(set(table) | set(jour)):
        a, b = table.get(gid), jour.get(gid)
        if a is None or b is None:
            out.append(f"room {room_id} game #{gid}: only in the {'journal' if a is None else 'tables'}")
            if b is None:
                stale.add(gid)
            continue
        for f in fields(a):
            if getattr(a, f.name) != getattr(b, f.name):
                out.append(f"room {room_id} game #{gid}.{f.name}: tables {getattr(a, f.name)!r} "
                           f"!= journal {getattr(b, f.name)!r}")
                stale.add(gid)
    return out, stale
