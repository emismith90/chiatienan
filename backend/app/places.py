"""Place identity + resolution, room-scoped.

Mirrors :mod:`app.roster`'s role for members, and deliberately stays a separate
module and a separate namespace: a place name must never resolve to a person
(design D18). ``roster._NameIndex`` searches **bank account holders**, so this
room's Nhím is reachable as "Trang" while the room eats at "Bún riêu cô Trang".
Keeping the indexes apart is what stops one being answered with the other.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Place
from app.roster import _fold, _tokens

logger = logging.getLogger("chiatienan")


def slugify(name: str) -> str:
    """``"Cơm gà Thịnh Lơ"`` -> ``"com-ga-thinh-lo"``.

    Delegates the hard part to :func:`roster._fold`, which already lowercases,
    strips Vietnamese tones, hand-maps ``đ`` (NFD leaves it whole) and squashes
    punctuation to spaces. This only joins the words.
    """
    return re.sub(r"\s+", "-", _fold(name)).strip("-")


#: Generic words Vietnamese puts in front of a venue name ("quán Bé Bự",
#: "chỗ bún chả"). Stripped only as a *fallback*, after the whole string has
#: failed — same discipline as ``roster._HONORIFICS``.
#:
#: Deliberately excludes "hàng" and "nhà": the room really does eat at "Bún Mọc
#: Hàng Lược" and the listing carries a "Nhà hàng Car Park", so stripping those
#: would eat part of a real name.
_PLACE_PREFIXES = {"quan", "cho", "tiem"}

#: Tiers narrow enough to write to the database on. Anything below this is a
#: guess: fine for a suggestion, never for a link (see ``backfill_links``).
CONFIDENT_TIERS = ("exact", "folded", "prefix")


def list_places(session: Session, room_id: int, *, include_inactive: bool = False) -> list[Place]:
    stmt = select(Place).where(Place.room_id == room_id)
    if not include_inactive:
        stmt = stmt.where(Place.active.is_(True))
    return list(session.scalars(stmt.order_by(Place.name)))


def _strip_place_prefix(tokens: list[str]) -> list[str]:
    """Drop leading venue words, never the last token (that IS the name)."""
    i = 0
    while i < len(tokens) - 1 and tokens[i] in _PLACE_PREFIXES:
        i += 1
    return tokens[i:]


class _PlaceIndex:
    """Every way a room's places can be named, indexed for lookup.

    Searchable fields: ``name``, ``slug``, every alias. Tiers run narrow→broad
    and the first non-empty one wins, so an exact hit is never widened into a
    token sweep — the same rule (and the same reason) as ``roster._NameIndex``.
    """

    def __init__(self, rows: list[Place]):
        self.places = list(rows)
        self.exact: dict[str, list[Place]] = {}
        self.folded: dict[str, list[Place]] = {}
        self.place_tokens: dict[int, set[str]] = {}
        for p in rows:
            self.place_tokens[p.id] = set()
            for raw in [p.name, p.slug.replace("-", " "), *(p.aliases or [])]:
                if not (raw or "").strip():
                    continue
                self._add(self.exact, (raw or "").strip().lower(), p)
                folded = _fold(raw)
                if not folded:
                    continue
                self._add(self.folded, folded, p)
                self.place_tokens[p.id].update(folded.split())

    @staticmethod
    def _add(index: dict[str, list[Place]], key: str, p: Place) -> None:
        bucket = index.setdefault(key, [])
        if p.id not in {x.id for x in bucket}:
            bucket.append(p)

    def lookup(self, raw: str) -> tuple[list[Place], str]:
        """``(candidates, tier)``; >1 candidate means genuinely ambiguous."""
        toks = _tokens(raw)
        if not toks:
            return [], "none"
        if hit := self.exact.get((raw or "").strip().lower()):
            return hit, "exact"
        if hit := self.folded.get(_fold(raw)):
            return hit, "folded"
        stripped = _strip_place_prefix(toks)
        if hit := self.folded.get(" ".join(stripped)):
            return hit, "prefix"
        want = set(stripped)
        hits = [p for p in self.places if want <= self.place_tokens[p.id]]
        return (hits, "tokens") if hits else ([], "none")


def resolve_one(session: Session, room_id: int, text: str) -> tuple[Place | None, str]:
    """The single best place ``text`` could mean, with the tier that matched.

    Returns ``(None, "ambiguous")`` when more than one place fits: the caller
    decides what to do, and for meal linking the answer is "nothing", since a
    silently wrong link moves money history onto the wrong restaurant.
    """
    hits, tier = _PlaceIndex(list_places(session, room_id)).lookup(text)
    if len(hits) == 1:
        return hits[0], tier
    if hits:
        return None, "ambiguous"
    return None, "none"


def resolve(session: Session, room_id: int, *, names: list[str] | None = None) -> dict:
    """Resolve free-text place names within ``room_id``.

    Same return shape as :func:`app.roster.resolve` so the two read alike at the
    call site — but they are separate namespaces and neither answers for the
    other (design D18).
    """
    index = _PlaceIndex(list_places(session, room_id))
    matched: dict[int, Place] = {}
    unresolved: list[str] = []
    ambiguous: list[dict] = []
    for raw in names or []:
        hits, _tier = index.lookup(raw)
        if len(hits) == 1:
            matched[hits[0].id] = hits[0]
        elif hits:
            ambiguous.append({
                "name": raw,
                "candidates": [{"id": p.id, "name": p.name, "slug": p.slug} for p in hits],
            })
        else:
            unresolved.append(raw)
    return {
        "matched": [{"id": p.id, "name": p.name, "slug": p.slug} for p in matched.values()],
        "unresolved": unresolved,
        "ambiguous": ambiguous,
    }
