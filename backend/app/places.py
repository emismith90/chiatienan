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

from sqlalchemy import func, select
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


class PlaceError(Exception):
    """A place write that cannot be applied."""


#: Columns a human may edit. **`slug` is not one of them**, and neither is
#: `former_slugs`: the slug is the `place:` subject in `observations.md` and the
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

    Shared by the ``add_place`` tool and the panel so the slug rule has one home.
    The tool treats a collision as success (it wanted the place to exist, and it
    does); the panel needs the collision surfaced, so this raises and the callers
    differ.
    """
    name = (name or "").strip()
    if not name:
        raise PlaceError("A place needs a name.")
    slug = slugify(name)
    if not slug:
        raise PlaceError(f"Cannot build an identifier from «{name}».")
    existing = session.scalars(
        select(Place).where(Place.room_id == room_id, Place.slug == slug)
    ).first()
    if existing is not None:
        raise PlaceError(f"«{existing.name}» is already on the list.")
    p = Place(room_id=room_id, slug=slug, name=name)
    apply_edits(p, fields)
    session.add(p)
    session.flush()
    return p


def rename_slug(session: Session, room_id: int, place_id: int, raw_slug: str) -> dict:
    """Change a place's room-scoped identity, moving everything filed under it.

    Three live stores hold a slug and all three move here: the row, the
    ``place:`` subjects in ``observations.md``, and the frozen subject on any
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

    place = session.get(Place, place_id)
    if place is None or place.room_id != room_id:
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
    place.former_slugs = [*formers, old]
    place.slug = new
    session.flush()

    notes = obs_mod.retarget_subject(room_id, old=f"place:{old}", new=f"place:{new}")
    memos_moved = memos_mod.retarget_subject(
        session, room_id, old=f"place:{old}", new=f"place:{new}",
        new_label=place.name)

    logger.info("[places] rename room=%s %s -> %s notes=%s memos=%s",
                room_id, old, new, notes, memos_moved)
    return {"changed": True, "slug": new, "former_slug": old,
            "notes_moved": notes["moved"], "notes_deduped": notes["deduped"],
            "memos_moved": memos_moved}


def apply_edits(place: Place, fields: dict) -> bool:
    """Assign the editable subset of ``fields`` onto ``place``. True if anything moved.

    Returning "did something change" is what lets the caller stay quiet about a
    no-op save instead of announcing an edit that edited nothing.
    """
    changed = False
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
        if getattr(place, key) != value:
            setattr(place, key, value)
            changed = True
    return changed


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
#: anyone actually going there (design D14).
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
