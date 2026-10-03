"""A room's poker games, read from the pack's ``games`` journal (plan 2026-10-02, B4).

Frozen records with the ORM rows' attribute names (``Game`` with ``.entries``), by id,
entries in the order they were recorded — what the ``games`` table gives back.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

from ledger_core.journal import load_date, load_datetime
from ledger_core.view import journal_events


@dataclass(frozen=True)
class EntryRecord:
    game_id: int
    member_id: int
    buy_in: int
    cash_out: int


@dataclass(frozen=True)
class GameRecord:
    id: int
    room_id: int
    played_on: date
    house: int = 0
    note: str | None = None
    raw_input: str | None = None
    source: str = "web"
    logged_by: str | None = None
    voided: bool = False
    voided_by: str | None = None
    voided_at: datetime | None = None
    created_at: datetime | None = None
    entries: tuple[EntryRecord, ...] = ()


def games(session, space_id, *, voided: bool | None = False, from_date: date | None = None,
          to_date: date | None = None) -> list[GameRecord]:
    """The room's games by id; ``voided=None`` for all; a window filters ``played_on``."""
    held: dict[int, GameRecord] = {}
    for ev in journal_events(session, "games", space_id):
        if ev["event"] == "game":
            held[ev["id"]] = GameRecord(
                id=ev["id"], room_id=ev["room_id"], played_on=load_date(ev["played_on"]), house=ev.get("house") or 0,
                note=ev.get("note"), raw_input=ev.get("raw_input"), source=ev.get("source") or "web",
                logged_by=ev.get("logged_by"), voided=bool(ev.get("voided")), voided_by=ev.get("voided_by"),
                voided_at=load_datetime(ev.get("voided_at")), created_at=load_datetime(ev.get("created_at")),
                entries=tuple(EntryRecord(ev["id"], e["member_id"], e["buy_in"], e["cash_out"])
                              for e in ev.get("entries") or []))
        elif ev["event"] == "game_void":
            if ev["game_id"] not in held:
                raise ValueError(f"games event 'game_void' is about game #{ev['game_id']} before the journal "
                                 "records it: the journal does not start at the beginning")
            held[ev["game_id"]] = replace(held[ev["game_id"]], voided=bool(ev["voided"]),
                                          voided_by=ev.get("voided_by"), voided_at=load_datetime(ev.get("voided_at")))
        else:
            raise ValueError(f"unknown games event {ev['event']!r}")
    out = []
    for g in sorted(held.values(), key=lambda g: g.id):
        if voided is not None and g.voided != voided:
            continue
        if to_date is not None and g.played_on > to_date:
            continue
        if from_date is not None and g.played_on < from_date:
            continue
        out.append(g)
    return out
