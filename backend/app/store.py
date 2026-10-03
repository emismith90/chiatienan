"""The app's internal stores on the data plane: notes and places (plan 2026-10-02, S2/S3).

Declared here, in code, and created by :meth:`app.db.Database.create_all` through
:func:`kernos.data.ensure_internal` — so every database (production, a test's, an eval
world's) has them before anything reads them, and nothing depends on a Kernel being
built (review R1).

Callers hand over their own ``session``: :func:`collection` resolves a store's
definition from it (cached per engine) and :data:`DATA` does the reads and writes
inside that session's transaction.
"""
from __future__ import annotations

import weakref

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernos.content import models as km
from kernos.data import SYSTEM_BUSINESS, DataStore, ensure_internal
from kernos.data.store import _row

_STR = {"type": "string"}

NOTES = {
    "slug": "notes", "name": "Notes",
    "description": "Per-room lunch memory: dated observations and standing rules (was observations.md).",
    "key": "id", "indexed": ["subject"], "searchable": ["text"],
    "schema": {"type": "object", "required": ["id", "seq", "when", "subject", "text"],
               "properties": {
                   "id": {"type": "string", "description": "line_id: a hash of the four fields"},
                   "seq": {"type": "integer", "description": "order within the room"},
                   "when": {"type": "string", "description": "ISO date, or 'always' for a standing rule"},
                   "subject": {"type": "string", "description": "place:<slug> or member:<nickname>"},
                   "gate": {"type": "string", "description": "busy@HH:MM, order-by@HH:MM or closes@HH:MM"},
                   "text": {"type": "string"}}},
}

PLACES = {
    "slug": "places", "name": "Places",
    "description": "Restaurants a room eats at. id is the integer meals.place_id points at.",
    "key": "id", "indexed": ["slug"], "searchable": ["name", "aliases", "tags", "address"],
    # The agent changes places only by proposing (a card a person confirms), through
    # `places.py`'s rules (its `Writer`): delete hides, ids come from the counter, and
    # the slug is not editable (a rename keeps the identity; `rename_slug` moves it).
    "options": {"confirm": True, "soft_delete": "active", "ids": "server",
                "editable": ["name", "aliases", "tags", "delivery", "address", "phone",
                             "walkable", "walk_minutes", "price_hint", "closed_until", "active"],
                "agent_tools": ["search", "create", "update", "delete"]},
    "schema": {"type": "object", "required": ["id", "slug", "name", "walkable", "active"],
               "properties": {
                   "id": {"type": "string", "description": "the integer id, as text"},
                   "slug": _STR, "name": _STR, "address": _STR, "phone": _STR,
                   "former_slugs": {"type": "array", "items": _STR},
                   "aliases": {"type": "array", "items": _STR},
                   "tags": {"type": "array", "items": _STR},
                   "delivery": {"type": "array", "items": _STR},
                   "walkable": {"type": "boolean"}, "active": {"type": "boolean"},
                   "walk_minutes": {"type": "integer"}, "price_hint": {"type": "integer"},
                   "closed_until": {"type": "string", "description": "ISO date"},
                   "created_at": {"type": "string", "description": "ISO datetime"}}},
}

#: One document per finished migration, in space "_": what ran, when, and its report.
MIGRATIONS = {
    "slug": "migrations", "name": "Migrations", "key": "id", "searchable": [],
    "schema": {"type": "object", "required": ["id", "done_at"],
               "properties": {"id": _STR, "done_at": _STR, "report": _STR}},
}

SPECS = [NOTES, PLACES, MIGRATIONS]


def _no_session():
    raise RuntimeError("app.store.DATA works only inside a caller's session")


#: Reads and writes for the internal stores; every call passes ``session=``.
DATA = DataStore(_no_session)

_collections: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def ensure(db) -> None:
    """Declare the stores in ``db`` (idempotent). Called by ``Database.create_all``."""
    ensure_internal(DataStore(db.session), db.session, SPECS)
    _collections.pop(db.engine, None)


def declared(slug: str) -> dict:
    """The code declaration of ``slug`` shaped like a stored definition, without its
    database id — enough to describe its tools before any database exists."""
    from kernos.data.search import default_searchable

    spec = next(sp for sp in SPECS if sp["slug"] == slug)
    return {"id": None, "slug": slug, "name": spec["name"], "description": spec.get("description", ""),
            "schema": spec["schema"], "key": spec["key"], "indexed": list(spec.get("indexed", ())),
            "mode": spec.get("mode", "table"), "internal": True, "options": spec.get("options"),
            "searchable": (default_searchable(spec["schema"]) if spec.get("searchable") is None
                           else list(spec["searchable"]))}


def collection(session: Session, slug: str) -> dict:
    """The internal collection ``slug`` of the database ``session`` is bound to."""
    engine = session.get_bind()
    cols = _collections.get(engine)
    if cols is None or slug not in cols:
        rows = session.scalars(
            select(km.Collection).join(km.Business, km.Business.id == km.Collection.business_id)
            .where(km.Business.slug == SYSTEM_BUSINESS, km.Collection.internal.is_(True))).all()
        cols = {r.slug: _row(r) for r in rows}
        _collections[engine] = cols
    if slug not in cols:
        raise RuntimeError(f"internal collection {slug!r} is missing: was Database.create_all() run?")
    return cols[slug]
