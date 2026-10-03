"""Test helpers for the places store (it used to be the `places` table)."""
from dataclasses import replace

from sqlalchemy import select

from app import places, store
from app.places import Place
from kernos.content import models as km
from kernos.data import DataStore


def add_place(s, *, room_id: int, slug: str, name: str, id: int | None = None, **fields) -> Place:
    """Insert a place as a test fixture, optionally with a pinned id. A pinned id also
    moves the id counter past it, so a later ``create_place`` cannot collide."""
    if id is None:
        return places.insert_place(s, room_id, slug=slug, name=name, **fields)
    DataStore.next_id("place", session=s, floor=id)
    return places._save(s, Place(id=id, room_id=room_id, slug=slug, name=name, **fields), create=True)


def place_by_id(s, place_id: int) -> Place | None:
    """A place by id, whatever its room (the old ``session.get(Place, id)``)."""
    col = store.collection(s, "places")
    space = s.scalar(select(km.Document.space_id).where(
        km.Document.collection_id == col["id"], km.Document.doc_id == str(place_id)))
    return None if space is None else places.get_place(s, int(space), place_id)


def place_by_slug(s, room_id: int, slug: str) -> Place:
    return next(p for p in places.list_places(s, room_id, include_inactive=True) if p.slug == slug)


def set_place(s, place_id: int, **fields) -> Place:
    """Change fields verbatim (the old ``row.attr = value``)."""
    return places.save_place(s, replace(place_by_id(s, place_id), **fields))
