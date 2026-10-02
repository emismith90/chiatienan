"""One-time import of places and notes into the data plane (plan 2026-10-02, release A).

    python -m app.migrate_storage --check  [--db URL]   # dry run: import, verify, roll back
    python -m app.migrate_storage --apply  [--db URL]   # import, verify, commit (once)

An explicit step, run by the deploy with the backend stopped — never at startup (review
R1): if it fails, the deploy stops and the previous image keeps running on untouched
data. It **copies, never moves**: the legacy ``places`` table and every
``observations.md`` are read, not changed. After a successful ``--apply`` commits, each
imported file is renamed ``observations.md.imported-<date>`` so nobody edits a file the
app no longer reads (review R8); the database copy is authoritative from then on.

Before committing, it checks its own work and refuses on any difference (review R5):
every legacy place row must equal its imported record field by field, and every room's
notes must equal the file's facts in file order. Comments, blank lines, unreadable lines
and exact duplicates are reported, never dropped silently.

Idempotent: a finished run leaves a ``migrations`` document (``storage-a``) and a second
``--apply`` is a no-op. Exit status: 0 done or already done, 1 refused, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import observations, places, store
from app.clock import now_ict

NAME = "storage-a"


def _legacy_place(row) -> places.Place:
    return places.Place(
        id=row.id, room_id=row.room_id, slug=row.slug, name=row.name,
        former_slugs=list(row.former_slugs or []), aliases=list(row.aliases or []),
        tags=list(row.tags or []), delivery=list(row.delivery or []), address=row.address,
        walkable=bool(row.walkable), walk_minutes=row.walk_minutes, phone=row.phone,
        price_hint=row.price_hint, closed_until=row.closed_until, active=bool(row.active),
        created_at=row.created_at)


def _done(session: Session) -> dict | None:
    return store.DATA.get_document(store.collection(session, "migrations"), "_", NAME, session=session)


def migrate(session: Session) -> dict:
    """Import, then verify. Raises :class:`MigrationRefused` on any difference; the
    caller rolls back. Returns the report."""
    from app.models import LegacyPlace, Room

    report = {"places": {}, "notes": {}, "problems": []}

    # ---- places: every legacy row, with its id
    legacy = session.scalars(select(LegacyPlace).order_by(LegacyPlace.id)).all()
    for row in legacy:
        p = _legacy_place(row)
        if places.get_place(session, p.room_id, p.id) is None:
            places._save(session, p, create=True)
        report["places"][str(p.room_id)] = report["places"].get(str(p.room_id), 0) + 1
    # No counter to seed: every new place's id is floored above the legacy table's max.

    # ---- notes: every room's file, in file order
    rooms = session.scalars(select(Room.id).order_by(Room.id)).all()
    expected_notes: dict[int, list] = {}
    for room_id in rooms:
        facts, parsed = observations.parse_file(observations.legacy_path(room_id))
        unique = list(dict.fromkeys(facts))           # an identical line is one fact
        expected_notes[room_id] = unique
        if not facts and not parsed["comments"] and not parsed["dropped"]:
            continue
        for o in unique:
            observations.append(session, room_id, o)
        report["notes"][str(room_id)] = {
            "imported": len(unique), "duplicates": len(facts) - len(unique),
            "comments": parsed["comments"], "blank": parsed["blank"],
            "dropped": [f"line {n}: {raw}" for n, raw in parsed["dropped"]]}

    # ---- verify before anything commits
    for row in legacy:
        want = _legacy_place(row)
        got = places.get_place(session, row.room_id, row.id)
        if got != want:
            report["problems"].append(f"place {row.id}: imported {got!r} != legacy {want!r}")
    for room_id, want in expected_notes.items():
        got = observations.load(session, room_id)
        if got != want:
            report["problems"].append(f"room {room_id} notes: {len(got)} imported != {len(want)} in the file "
                                      f"(or out of order)")
    if report["problems"]:
        raise MigrationRefused(report)
    return report


class MigrationRefused(Exception):
    def __init__(self, report: dict) -> None:
        super().__init__("; ".join(report["problems"]))
        self.report = report


def run(db, *, apply: bool) -> tuple[int, dict]:
    """``(exit status, report)``. ``--check`` never commits."""
    s = db._sessionmaker()
    try:
        if _done(s) is not None:
            return 0, {"status": "already applied"}
        report = migrate(s)
        if not apply:
            s.rollback()
            return 0, {"status": "check passed (rolled back)", **report}
        store.DATA.upsert_document(store.collection(s, "migrations"), "_",
                                   {"id": NAME, "done_at": now_ict().isoformat(),
                                    "report": json.dumps(report, ensure_ascii=False)},
                                   actor="migrate_storage", session=s)
        s.commit()
    except MigrationRefused as exc:
        s.rollback()
        return 1, {"status": "refused: nothing was written", **exc.report}
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    _retire_files(report)
    return 0, {"status": "applied", **report}


def _retire_files(report: dict) -> None:
    """After the commit: rename each imported file so nobody edits a dead file. A
    failure here costs nothing — the app no longer reads the file either way."""
    stamp = date.today().isoformat()
    for room_id in report["notes"]:
        path = observations.legacy_path(int(room_id))
        if path.exists():
            try:
                path.rename(path.with_name(f"observations.md.imported-{stamp}"))
            except OSError as exc:                  # pragma: no cover — reported, not fatal
                print(f"note: could not rename {path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.migrate_storage", description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="import and verify, then roll back")
    mode.add_argument("--apply", action="store_true", help="import, verify and commit (once)")
    ap.add_argument("--db", help="database URL (default: DATABASE_URL)")
    args = ap.parse_args(argv)

    from app.config import settings
    from app.db import Database

    db = Database(args.db or settings.database_url)
    db.create_all()                       # the stores exist before anything is imported
    status, report = run(db, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
