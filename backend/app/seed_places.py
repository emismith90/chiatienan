"""Load curated place seeds into a room, and lint them first.

Run once per room after deploy::

    python -m app.seed_places 7 seeds/places-local.json seeds/places-nearby.json seeds/places-online.json

Idempotent by ``(room_id, slug)``: re-running refreshes the curated fields and
never duplicates a row, so the seed files stay the editable source of truth.
Finishes by backfilling ``meals.place_id`` over the room's history — without
that, "tuần này ăn bún mấy lần rồi" has nothing to count until months of new
meals accumulate.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Place
from app.places import slugify
from app.roster import _fold, _strip_honorific, _tokens

logger = logging.getLogger("chiatienan")

_FIELDS = ("name", "address", "aliases", "tags", "delivery", "walkable",
           "walk_minutes", "phone", "price_hint", "active")

#: Place aliases that legitimately reduce to a member's name. "Bún riêu cô
#: Trang" is genuinely how the room names that restaurant, and this room's Nhím
#: banks as DINH HONG TRANG — deleting real speech to dodge the collision moves
#: the bug rather than fixing it, so it is guarded by a test instead (D18).
_LINT_EXCEPTIONS = {"co trang"}


def lint(rows: list[dict], *, member_names: list[str]) -> list[str]:
    """Problems that must be fixed before seeding; empty list means clean.

    The only rule so far, and the one that caught a real defect: no place alias
    may reduce to a bare member nickname or given name. ``roster._NameIndex``
    searches bank account holders, so such an alias makes a *place* answer a
    lookup for a *person* — and via ``_dropped_names`` that can add the wrong
    eater to a split. The failure mode is money, not a bad suggestion.
    """
    banned: set[str] = set()
    for n in member_names:
        banned.update(_strip_honorific(_tokens(n)))
    problems = []
    for row in rows:
        for alias in row.get("aliases") or []:
            reduced = _strip_honorific(_tokens(alias))
            if len(reduced) == 1 and reduced[0] in banned and _fold(alias) not in _LINT_EXCEPTIONS:
                problems.append(
                    f"{row.get('name')!r}: alias {alias!r} reduces to {reduced[0]!r}, "
                    f"which is a member name — a place must never resolve to a person."
                )
    return problems


def load_file(session: Session, room_id: int, path) -> dict:
    """Upsert one seed file's places into ``room_id``. Returns created/updated counts."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    created = updated = 0
    for row in rows:
        slug = row.get("slug") or slugify(row["name"])
        existing = session.scalars(
            select(Place).where(Place.room_id == room_id, Place.slug == slug)
        ).first()
        values = {k: row[k] for k in _FIELDS if k in row}
        if cu := row.get("closed_until"):
            values["closed_until"] = date.fromisoformat(cu)
        if existing is None:
            session.add(Place(room_id=room_id, slug=slug, **values))
            created += 1
        else:
            for k, v in values.items():
                setattr(existing, k, v)
            updated += 1
    session.flush()
    return {"created": created, "updated": updated}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    from app.db import get_db
    from app.places import backfill_links
    from app.roster import list_members

    room_id, paths = int(argv[1]), [Path(p) for p in argv[2:]]
    rows = [r for p in paths for r in json.loads(p.read_text(encoding="utf-8"))]
    db = get_db()
    with db.session() as s:
        names = [n for m in list_members(s, room_id, include_inactive=True)
                 for n in (m.display_name, m.nickname, m.account_holder or "")]
        if problems := lint(rows, member_names=[n for n in names if n]):
            for p in problems:
                print(f"LINT: {p}", file=sys.stderr)
            return 1
        totals = {"created": 0, "updated": 0}
        for path in paths:
            res = load_file(s, room_id, path)
            print(f"{path.name}: +{res['created']} ~{res['updated']}")
            totals = {k: totals[k] + res[k] for k in totals}
        print(f"total: +{totals['created']} ~{totals['updated']}")
        print(f"backfill: {backfill_links(s, room_id)}")
    obs_seed = paths[0].parent / "observations-local.md"
    if obs_seed.exists():
        print(f"observations: {install_observations(room_id, obs_seed)}")
    return 0


def install_observations(room_id: int, path) -> dict:
    """Copy a seed observations file into the room, skipping lines already there.

    Idempotent and non-destructive: notes the room has accumulated since the last
    seed are never clobbered, and re-running only adds what is missing.
    """
    from app import observations

    existing = {(o.subject, o.text) for o in observations.load(room_id)}
    added = skipped = 0
    for i, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        o = observations._parse_line(raw, i)
        if o is None:
            continue
        if (o.subject, o.text) in existing:
            skipped += 1
            continue
        observations.append(room_id, o)
        existing.add((o.subject, o.text))
        added += 1
    return {"added": added, "skipped": skipped}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
