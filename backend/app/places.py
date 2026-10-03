"""Place identity + resolution, room-scoped.

Mirrors :mod:`app.roster`'s role for members, and deliberately stays a separate
module and a separate namespace: a place name must never resolve to a person
(design D18). ``roster._NameIndex`` searches **bank account holders**, so this
room's Nhím is reachable as "Trang" while the room eats at "Bún riêu cô Trang".
Keeping the indexes apart is what stops one being answered with the other.

Stored in the ``places`` internal collection (:mod:`app.store`, plan 2026-10-02 S3),
one document per place, its id the integer ``meals.place_id`` points at (as text).
A :class:`Place` is a frozen record: a write is :func:`create_place`,
:func:`edit_place`, :func:`rename_slug` or :func:`insert_place` (the seed CLI),
never an attribute assignment — a missed save raises instead of silently doing
nothing. The old ``places`` table (:class:`app.models.LegacyPlace`) is read only by
the one-time import.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import store
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


class PlaceError(Exception):
    """A place write that cannot be applied."""


@dataclass(frozen=True)
class Place:
    """One restaurant of one room — the attribute names of the old ``places`` row.

    Identity for the free text in ``meals.dish``: "bún chả rửa xe", "Bún chả" and
    "bun cha" are three strings for one business, and nothing can be counted until
    they point at one place. ``slug`` is that identity — stable, ASCII, and used
    verbatim as the ``place:`` subject in the notes. ``former_slugs`` is every slug
    it was known by, oldest first, appended by :func:`rename_slug` only.

    No price field on purpose (design D8): ``meals.total_amount ÷ heads`` is what
    the group actually paid. ``price_hint`` is only a seed-time fallback.
    """
    id: int
    room_id: int
    slug: str
    name: str
    former_slugs: list = field(default_factory=list)
    aliases: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    delivery: list = field(default_factory=list)
    address: str | None = None
    #: Walkability is a property of the seed list (D17): the room's list IS the walk-to set.
    walkable: bool = True
    #: Optional override of the room-wide default used by the clock gates.
    walk_minutes: int | None = None
    #: Passed through verbatim, never retyped by the model (D10).
    phone: str | None = None
    price_hint: int | None = None          # VND per head
    #: Temporary closures self-expire (D11); ``active=False`` is for permanent ones.
    closed_until: date | None = None
    active: bool = True
    created_at: datetime | None = None


def _from_doc(room_id: int, doc: dict) -> Place:
    d = doc["data"]
    return Place(
        id=int(d["id"]), room_id=int(room_id), slug=d["slug"], name=d["name"],
        former_slugs=list(d.get("former_slugs") or []), aliases=list(d.get("aliases") or []),
        tags=list(d.get("tags") or []), delivery=list(d.get("delivery") or []),
        address=d.get("address"), walkable=bool(d["walkable"]), walk_minutes=d.get("walk_minutes"),
        phone=d.get("phone"), price_hint=d.get("price_hint"),
        closed_until=date.fromisoformat(d["closed_until"]) if d.get("closed_until") else None,
        active=bool(d["active"]),
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else None)


def _to_data(p: Place) -> dict:
    data = {"id": str(p.id), "slug": p.slug, "name": p.name, "former_slugs": list(p.former_slugs),
            "aliases": list(p.aliases), "tags": list(p.tags), "delivery": list(p.delivery),
            "walkable": bool(p.walkable), "active": bool(p.active)}
    for key in ("address", "phone", "walk_minutes", "price_hint"):
        if getattr(p, key) is not None:
            data[key] = getattr(p, key)
    if p.closed_until is not None:
        data["closed_until"] = p.closed_until.isoformat()
    if p.created_at is not None:
        data["created_at"] = p.created_at.isoformat()
    return data


def _save(session: Session, p: Place, *, create: bool = False) -> Place:
    """Write ``p``. The slug is the document's ``unique_key``, so the database refuses
    two places of one room on one slug even from a writer outside the app's lock."""
    from kernos.content.errors import Conflict

    write = store.DATA.insert_document if create else store.DATA.upsert_document
    try:
        write(store.collection(session, "places"), p.room_id, _to_data(p), actor="places",
              session=session, unique_key=p.slug)
    except Conflict as exc:
        raise PlaceError(f"«{p.slug}» is already taken in this room.") from exc
    return p


def _next_id(session: Session) -> int:
    """A place id above every id the old table ever used, so ``meals.place_id``
    can never come to mean two places."""
    from app.models import LegacyPlace

    floor = session.scalar(select(func.max(LegacyPlace.id))) or 0
    return store.DATA.next_id("place", session=session, floor=floor)


def get_place(session: Session, room_id: int, place_id: int) -> Place | None:
    doc = store.DATA.get_document(store.collection(session, "places"), room_id, str(place_id),
                                  session=session)
    return _from_doc(room_id, doc) if doc else None


def insert_place(session: Session, room_id: int, *, slug: str, **values) -> Place:
    """Create a place with a pinned slug and values taken verbatim — the seed CLI's
    path (its files are curated; nothing is renormalised)."""
    from app.clock import now_ict

    p = Place(id=_next_id(session), room_id=room_id, slug=slug, created_at=now_ict(), **values)
    return _save(session, p, create=True)


def save_place(session: Session, place: Place) -> Place:
    """Write a place back after ``dataclasses.replace`` — the seed CLI's update path."""
    return _save(session, place)


#: Columns a human may edit. **`slug` is not one of them**, and neither is
#: `former_slugs`: the slug is the `place:` subject in the room's notes and the
#: key `seed_places` matches on, so changing it as a field would silently detach
#: every note and standing rule about that restaurant. `name` and `aliases` carry
#: an ordinary renaming.
#:
#: A genuine identity change goes through :func:`rename_slug`, which migrates the
#: stores that hold the old value instead of leaving them behind. It is a separate
#: function — and a separate route — so that this tuple stays literally true and no
#: client can rename by round-tripping a form.
EDITABLE = (
    "name", "aliases", "tags", "delivery", "address", "phone",
    "walkable", "walk_minutes", "price_hint", "closed_until", "active",
)

_LIST_FIELDS = ("aliases", "tags", "delivery")


def create_place(session: Session, room_id: int, *, name: str, **fields) -> Place:
    """Insert a place, or raise if its slug is taken.

    Shared by the agent's ``places_create`` (through :func:`_apply_action`) and the
    panel so the slug rule has one home. A collision raises: the panel surfaces it,
    and the agent's proposal is refused before its card is shown (:func:`_check_action`).
    """
    name = (name or "").strip()
    if not name:
        raise PlaceError("A place needs a name.")
    slug = slugify(name)
    if not slug:
        raise PlaceError(f"Cannot build an identifier from «{name}».")
    existing = next((p for p in list_places(session, room_id, include_inactive=True)
                     if p.slug == slug), None)
    if existing is not None:
        raise PlaceError(f"«{existing.name}» is already on the list.")
    from app.clock import now_ict

    p, _changed = _edited(Place(id=_next_id(session), room_id=room_id, slug=slug, name=name,
                                created_at=now_ict()), fields)
    return _save(session, p, create=True)


def rename_slug(session: Session, room_id: int, place_id: int, raw_slug: str) -> dict:
    """Change a place's room-scoped identity, moving everything filed under it.

    Three live stores hold a slug and all three move here: the row, the
    ``place:`` subjects in the room's notes, and the frozen subject on any
    **pending** memo card. The two *offline* stores — ``seeds/places-*.json`` and
    ``seeds/observations-local.md`` — are not rewritten (a droplet's DB has long
    since diverged from files in the repo, and an HTTP route must not edit them);
    instead the old slug is remembered in ``former_slugs`` so their readers find
    this row rather than manufacturing a duplicate. See :mod:`app.seed_places`.

    The new slug is **typed, not derived from the name**. 12 of production's 100
    places carry a curated slug that is not ``slugify(name)`` — ``be-bu`` for
    "Quán Bé Bự - Khoai Tây", ``com-ga-thinh-lo`` for "Cơm gà đảo, cơm rang Thịnh
    Lơ" — so recomputing on rename would undo curation nobody asked to undo. What
    is typed still goes through :func:`slugify`, because ``roster._fold`` maps
    ``đ→d`` by hand (NFD leaves it whole) and a second normaliser gets that wrong.

    Renaming *onto* a slug another place holds, live or former, is refused rather
    than merged: merging two places also has to reassign ``meals.place_id`` and
    reconcile both histories, which is a different and much larger feature.

    Returns ``{"changed", "slug", "former_slug", "notes_moved", "notes_deduped",
    "memos_moved"}``.
    """
    # Local imports: `memos` imports `chat`, and a module-level import here would
    # risk a cycle. `observations` is only needed on the write path.
    from app import memos as memos_mod, observations as obs_mod

    place = get_place(session, room_id, place_id)
    if place is None:
        raise PlaceError("No such place.")

    new = slugify(raw_slug or "")
    if not new:
        raise PlaceError(f"Cannot build an identifier from «{raw_slug}».")

    old = place.slug
    if new == old:
        # "Quán Bé Bự" → "quán bé bự" is the same identity. Rewriting the memory
        # file for nothing is not free: every line's `line_id` would churn.
        return {"changed": False, "slug": old, "former_slug": None,
                "notes_moved": 0, "notes_deduped": 0, "memos_moved": 0}

    for other in list_places(session, room_id, include_inactive=True):
        if other.id == place.id:
            continue
        if other.slug == new:
            raise PlaceError(f"«{other.name}» already uses «{new}».")
        if new in (other.former_slugs or []):
            raise PlaceError(f"«{other.name}» used «{new}» before — pick another.")

    # Renaming back to a slug this place previously held retires it from the
    # former list: two rows must never both answer to one slug, and that includes
    # one row answering to it twice.
    formers = [f for f in (place.former_slugs or []) if f not in (new, old)]
    place = _save(session, replace(place, former_slugs=[*formers, old], slug=new))

    notes = obs_mod.retarget_subject(session, room_id, old=f"place:{old}", new=f"place:{new}")
    memos_moved = memos_mod.retarget_subject(
        session, room_id, old=f"place:{old}", new=f"place:{new}",
        new_label=place.name)

    logger.info("[places] rename room=%s %s -> %s notes=%s memos=%s",
                room_id, old, new, notes, memos_moved)
    return {"changed": True, "slug": new, "former_slug": old,
            "notes_moved": notes["moved"], "notes_deduped": notes["deduped"],
            "memos_moved": memos_moved}


def edit_place(session: Session, place: Place, fields: dict) -> tuple[Place, bool]:
    """Apply the editable subset of ``fields`` and save. ``(place, changed)``.

    Returning "did something change" is what lets the caller stay quiet about a
    no-op save instead of announcing an edit that edited nothing — and a no-op
    writes nothing.
    """
    updated, changed = _edited(place, fields)
    if changed:
        _save(session, updated)
    return updated, changed


def _edited(place: Place, fields: dict) -> tuple[Place, bool]:
    """``place`` with the editable subset of ``fields`` applied, and whether it moved."""
    changes = {}
    for key in EDITABLE:
        if key not in fields:
            continue
        value = fields[key]
        if key == "name":
            if not (value or "").strip():
                raise PlaceError("A place needs a name.")
            # The slug is not recomputed (see EDITABLE): renaming keeps the identity.
            value = value.strip()
        if key in _LIST_FIELDS:
            value = [str(v).strip() for v in (value or []) if str(v).strip()]
        elif isinstance(value, str):
            value = value.strip() or None
        if key in ("walkable", "active"):
            if value is None:
                continue                  # a flag has no "unset"
            value = bool(value)
        if key == "closed_until" and isinstance(value, str):
            value = date.fromisoformat(value)
        if getattr(place, key) != value:
            changes[key] = value
    return (replace(place, **changes), True) if changes else (place, False)


def list_places(session: Session, room_id: int, *, include_inactive: bool = False) -> list[Place]:
    """The room's places by name (code-point order, as the old ``ORDER BY name``),
    then id."""
    rows = [_from_doc(room_id, d) for d in
            store.DATA.read_all(store.collection(session, "places"), room_id, session=session)]
    if not include_inactive:
        rows = [p for p in rows if p.active]
    return sorted(rows, key=lambda p: (p.name, p.id))


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


def backfill_links(session: Session, room_id: int) -> dict:
    """Link historical meals to places by resolving ``meals.dish``.

    **Confident tiers only.** A backfill has no draft card, so no human reviews
    the guess before it is written — and a wrong link silently moves a meal's
    money history onto another restaurant, which no later correction catches
    because nothing looks broken. An unlinked meal is the cheaper failure.

    Idempotent: meals that already carry a ``place_id`` are never revisited, so
    re-running after adding aliases only picks up what was previously missed.
    """
    from app.models import Meal

    index = _PlaceIndex(list_places(session, room_id))
    rows = session.scalars(
        select(Meal).where(
            Meal.room_id == room_id,
            Meal.place_id.is_(None),
            Meal.voided.is_(False),
            Meal.dish.isnot(None),
        )
    ).all()

    counts = {"linked": 0, "skipped": 0, "ambiguous": 0}
    for meal in rows:
        hits, tier = index.lookup(meal.dish or "")
        if len(hits) > 1:
            counts["ambiguous"] += 1
        elif len(hits) == 1 and tier in CONFIDENT_TIERS:
            meal.place_id = hits[0].id
            counts["linked"] += 1
        else:
            counts["skipped"] += 1
    session.flush()
    logger.info("[places] backfill room=%s %s", room_id, counts)
    return counts


#: How much history a suggestion looks at. Long enough to see a weekday rhythm,
#: short enough that a place the room dropped six months ago stops competing.
_STATS_WINDOW_DAYS = 120

#: Price bands: cheap / mid / pricey. The Vietnamese words are the enum values
#: the `suggest_lunch` schema and stored stats use, so they stay.
_BANDS = ("rẻ", "vừa", "đắt")


def _band_for(value: int | None, thresholds: tuple[int, int] | None) -> str | None:
    """Which tertile ``value`` falls in, or None when it cannot be placed."""
    if value is None or thresholds is None:
        return None
    low, high = thresholds
    if value <= low:
        return _BANDS[0]
    return _BANDS[1] if value <= high else _BANDS[2]


def stats(session: Session, room_id: int, *, window_days: int = _STATS_WINDOW_DAYS,
          today=None) -> dict[int, dict]:
    """Per-place counts, recency, weekday rhythm and price band, from the ledger.

    Every number a suggestion rests on is computed here, in Python (design D1):
    a model that eyeballs "we ate bún chả 3 times" is wrong eventually, and a
    confidently wrong count poisons trust in everything else the bot says.

    ``avg_per_head`` divides by **members only**. ``Meal.total_amount`` is
    ``tracked_total`` — :func:`app.money.split_with_guests` already computed the
    per-head over members plus guests, billed the members and dropped the guest
    heads as settled in cash. Dividing again by members+guests would understate
    the per-head cost by exactly the guest fraction, making every place where
    guests join look cheaper than it is.

    ``band`` is a tertile **across this room's own places**, not absolute VND, so
    it stays meaningful as prices drift. ``price_hint`` fills in for a place with
    no linked meals yet and is dropped the moment real history exists (D8).
    """
    from datetime import timedelta

    from app.clock import today_ict
    from app.models import Meal, MealShare

    today = today or today_ict()
    cutoff = today - timedelta(days=window_days)
    rows = list_places(session, room_id, include_inactive=True)

    out: dict[int, dict] = {
        p.id: {"times": 0, "last_on": None, "days_since": None,
               "weekday_counts": {i: 0 for i in range(7)},
               "avg_per_head": None, "band": None}
        for p in rows
    }
    totals: dict[int, list[int]] = {p.id: [] for p in rows}

    meals = session.scalars(
        select(Meal).where(
            Meal.room_id == room_id,
            Meal.place_id.isnot(None),
            Meal.voided.is_(False),
            Meal.occurred_on >= cutoff,
        )
    ).all()

    for meal in meals:
        entry = out.get(meal.place_id)
        if entry is None:
            continue                      # a place from another room, or deleted
        entry["times"] += 1
        if entry["last_on"] is None or meal.occurred_on > entry["last_on"]:
            entry["last_on"] = meal.occurred_on
        entry["weekday_counts"][meal.occurred_on.weekday()] += 1
        heads = session.scalar(
            select(func.count()).select_from(MealShare).where(MealShare.meal_id == meal.id)
        ) or 0
        if heads:
            totals[meal.place_id].append(meal.total_amount // heads)

    for p in rows:
        entry = out[p.id]
        if entry["last_on"] is not None:
            entry["days_since"] = (today - entry["last_on"]).days
        seen = totals[p.id]
        entry["avg_per_head"] = (sum(seen) // len(seen)) if seen else p.price_hint

    # Tertiles over whatever prices we know, so a room of six cheap places still
    # gets a spread rather than everything landing in one band.
    known = sorted(v for v in (out[p.id]["avg_per_head"] for p in rows) if v is not None)
    thresholds = None
    if len(known) >= 3:
        # Boundaries index off (n-1), so the top tertile is genuinely the top:
        # with [30k, 70k, 200k], `n//3` would put 200k at the "vừa" ceiling and
        # leave "đắt" unreachable.
        last = len(known) - 1
        thresholds = (known[last // 3], known[(2 * last) // 3])
    for p in rows:
        out[p.id]["band"] = _band_for(out[p.id]["avg_per_head"], thresholds)
    return out


#: Tag a place carries when it came from a directory import rather than from
#: anyone actually going there (design D14). "chưa-thử" = "not tried yet"; it is a
#: stored tag value, so it stays in Vietnamese.
UNTRIED_TAG = "chưa-thử"


def resolve_best(session: Session, room_id: int, text: str, *, today=None
                 ) -> tuple[Place | None, str]:
    """Like :func:`resolve_one`, but decides an ambiguity instead of refusing it.

    With 100 seeded places, 17 plain-dish queries are ambiguous — "bánh cuốn"
    matches 6, "nem nướng" 5 — because a directory import adds places nobody has
    been to. Asking every time is worse than not importing at all, so candidates
    are ordered by (1) not ``chưa-thử``, (2) meal count, and the leader wins if
    it beats the runner-up outright. A genuine tie still returns ``ambiguous``
    and still asks (design D15).

    **Suggestions use this; meal linking does not.** A wrong suggestion costs one
    bad lunch idea, while a wrong link moves money history onto the wrong
    restaurant with no card for anyone to catch it — see :func:`backfill_links`.
    """
    hits, tier = _PlaceIndex(list_places(session, room_id)).lookup(text)
    if len(hits) == 1:
        return hits[0], tier
    if not hits:
        return None, "none"

    counts = stats(session, room_id, today=today)
    def rank(p: Place) -> tuple[int, int]:
        return (0 if UNTRIED_TAG in (p.tags or []) else 1,
                counts.get(p.id, {}).get("times", 0))

    ordered = sorted(hits, key=rank, reverse=True)
    if rank(ordered[0]) > rank(ordered[1]):
        return ordered[0], "best"
    return None, "ambiguous"


# ------------------------------------------------------------ the agent's actions
#
# The agent changes places through the generic data-plane tools (`places_create`,
# `places_update`, `places_delete`), which only *propose*: a person confirms the card
# (kernos.data.actions). These two functions are the places' rules for that path —
# the same `create_place` / `_edited` / `edit_place` the panel uses, so the chat and
# the panel can never disagree about what a valid place is.

def _action_fields(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k != "name"}


def _check_action(session: Session, room_id, op: str, before: dict | None, payload: dict) -> dict:
    """The place as the action would leave it (``Writer.check``)."""
    from kernos.content.errors import Invalid

    room_id = int(room_id)
    try:
        if op == "create":
            name = (payload.get("name") or "").strip()
            slug = slugify(name)
            if not name or not slug:
                raise PlaceError("A place needs a name.")
            existing = next((p for p in list_places(session, room_id, include_inactive=True)
                             if p.slug == slug), None)
            if existing is not None:
                hint = " It is hidden: update it with active=true to bring it back." if not existing.active else ""
                raise PlaceError(f"«{existing.name}» is already on the list (id {existing.id}).{hint}")
            p, _ = _edited(Place(id=0, room_id=room_id, slug=slug, name=name), _action_fields(payload))
            data = _to_data(p)
            del data["id"]                      # drawn from the counter when confirmed
            return data
        place = _from_doc(room_id, {"data": before})
        if op == "update":
            p, changed = _edited(place, payload)
            if not changed:
                raise PlaceError(f"That would not change «{place.name}».")
            return _to_data(p)
        if op == "delete":
            if not place.active:
                raise PlaceError(f"«{place.name}» is already hidden.")
            return _to_data(replace(place, active=False))
    except (PlaceError, ValueError) as exc:
        raise Invalid(str(exc)) from exc
    raise Invalid(f"places cannot {op}")


def _apply_action(session: Session, room_id, op: str, before: dict | None, payload: dict, actor: str) -> str:
    """Perform a confirmed action (``Writer.apply``); the place's id."""
    from kernos.content.errors import Invalid

    room_id = int(room_id)
    try:
        if op == "create":
            return str(create_place(session, room_id, name=payload["name"], **_action_fields(payload)).id)
        place = _from_doc(room_id, {"data": before})
        edit_place(session, place, payload if op == "update" else {"active": False})
        return str(place.id)
    except (PlaceError, ValueError) as exc:
        raise Invalid(str(exc)) from exc


def _register_writer() -> None:
    from kernos.data.actions import Writer, register_writer

    register_writer("places", Writer(check=_check_action, apply=_apply_action,
                                     identity=lambda after: after["slug"]))


_register_writer()
