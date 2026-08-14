# Phoenix Lunch Suggestion — Phase 1: Place Identity & Meal Linking

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every restaurant a stable identity, resolve the room's casual spellings onto it, and link meals to it — so the months of free-text `meals.dish` already in the ledger become countable.

**Architecture:** A new `places` table plus a nullable `meals.place_id`. A `_PlaceIndex` matcher mirrors `roster._NameIndex` (tone-folding, tiered narrow→broad lookup) but stays a separate class and a separate namespace — a place name must never resolve to a person. `propose_meal` attaches a confident place guess to the draft the user already confirms; a one-shot backfill links history on confident matches only.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (SQLite/WAL), pytest.

**Spec:** [`docs/superpowers/specs/2026-08-14-lunch-suggestion-memory-design.md`](../specs/2026-08-14-lunch-suggestion-memory-design.md)

**Phase 1 of 3.** Phase 2 (ledger stats + `suggest_lunch` + skill) and Phase 3 (observations file, clock gates, `remember`/`forget`, memo card) get their own plans once this lands — Phase 1's real matching behaviour informs Phase 2's ranking.

## Global Constraints

- **Place resolution NEVER blocks a meal (D2).** This is the opposite of `_dropped_names` (`tools.py:88`), which refuses loudly for unresolved eaters. A missing eater bills everyone wrong; a missing place tag costs a statistic. No place failure may return `_err()` from `propose_meal`.
- **Places and people are separate namespaces (D18).** `places.resolve_*` must never return a `Member`; `roster.resolve` must never return a `Place`. `roster._NameIndex` indexes **bank account holders**, so this room's Nhím (`DINH HONG TRANG`) is reachable as *Trang* while the room eats at *Bún riêu cô Trang*. Task 7 locks this down.
- **Backfill links on confident tiers only** (`exact`/`folded`/`prefix`). No human reviews a backfill, and a silent wrong link moves money history onto the wrong restaurant.
- **Money is untouched.** No change to amounts, shares, splits, or settlement. `place_id` is metadata that rides alongside.
- **Schema is additive only.** `create_all()` builds `places`; `_sync_additive_columns` (`db.py:64`) adds `meals.place_id`. Note it emits `ALTER TABLE … ADD COLUMN place_id INTEGER` with **no FK constraint** on pre-existing DBs — that is expected and harmless (SQLite cannot add a constrained column), so never write a test asserting the FK exists on an upgraded database.
- **Vietnamese, tone-insensitive.** Reuse `roster._fold` / `roster._tokens`; never write a second folding implementation.
- **Tests:** TDD — failing test first, watch it fail, minimal implementation, watch it pass, commit. Run from `backend/` with the venv active: `cd backend && source .venv/bin/activate`.

## File Structure

- `backend/app/models.py` — **modify**: add `Place`; add `Meal.place_id`.
- `backend/app/places.py` — **create**: `slugify`, `_PlaceIndex`, `resolve_one`, `resolve`, `list_places`, `backfill_links`. The whole place domain, mirroring `roster.py`'s role for members.
- `backend/app/seed_places.py` — **create**: `python -m app.seed_places` loader + seed lint.
- `backend/app/tools.py` — **modify**: add `find_places`, `add_place`; attach the place guess in `propose_meal`.
- `backend/app/drafts.py` — **modify**: `_EDITABLE` gains `place_id`; pass it through to `record_meal`.
- `backend/app/ledger.py` — **modify**: `record_meal` accepts and persists `place_id`.
- `backend/tests/` — **create**: `test_places_resolve.py`, `test_seed_places.py`, `test_place_tools.py`, `test_meal_place_link.py`, `test_place_backfill.py`, `test_name_space_separation.py`.

---

### Task 1: `Place` model, `Meal.place_id`, and `slugify`

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/app/places.py`
- Test: `backend/tests/test_places_resolve.py` (slug half only)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `models.Place` with columns `id, room_id, slug, name, aliases, tags, delivery, address, walkable, walk_minutes, phone, price_hint, closed_until, active, created_at`.
  - `models.Meal.place_id: int | None`.
  - `places.slugify(name: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_places_resolve.py
from app.places import slugify


def test_slugify_folds_tones_and_hyphenates():
    assert slugify("Cơm gà Thịnh Lơ") == "com-ga-thinh-lo"
    assert slugify("Quán Bé Bự - Khoai Tây") == "quan-be-bu-khoai-tay"
    assert slugify("Phở Vui") == "pho-vui"


def test_slugify_maps_d_stroke_and_drops_punctuation():
    # đ does not decompose under NFD — roster._fold maps it by hand, and slugify
    # inherits that. "Đặng Văn Ngữ" must not become "ang-van-ngu".
    assert slugify("Bún cá Đặng Văn Ngữ") == "bun-ca-dang-van-ngu"
    assert slugify("Jacky - Mì Vịt Quay & Cơm Xá Xíu") == "jacky-mi-vit-quay-com-xa-xiu"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_places_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.places'`

- [ ] **Step 3: Add the model**

```python
# backend/app/models.py — after the Member class

class Place(Base):
    """A restaurant the room can eat at or order from.

    Identity for the free text in ``meals.dish``: "bún chả rửa xe", "Bún chả"
    and "bun cha" are three strings for one business, and nothing can be counted
    until they point at one row. ``slug`` is that identity — stable, ASCII, and
    used verbatim as the ``place:`` subject in the observations file (Phase 3).

    No price column on purpose (design D8): ``meals.total_amount ÷ heads`` is
    what the group actually paid, after discounts. ``price_hint`` is only a
    seed-time fallback for a place nobody has eaten at yet.
    """
    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("room_id", "slug", name="uq_room_place_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    delivery: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    address: Mapped[str | None] = mapped_column(String(200))
    # Walkability is a property of the seed list, not of each row (D17): the
    # room's own list IS the walk-to set. 76 True / 24 False at seed time.
    walkable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Optional override of the room-wide default used by Phase 3's clock gates.
    walk_minutes: Mapped[int | None] = mapped_column(Integer)
    # Passed through verbatim, never retyped by the model (D10): a digit changed
    # by hand is a wrong number nobody notices until they call it.
    phone: Mapped[str | None] = mapped_column(String(20))
    price_hint: Mapped[int | None] = mapped_column(Integer)          # VND per head
    # Temporary closures self-expire (D11); `active=False` is for permanent ones.
    closed_until: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_ict)
```

Add to `Meal`, directly after the existing `dish` column:

```python
    place_id: Mapped[int | None] = mapped_column(ForeignKey("places.id"), nullable=True, index=True)
```

- [ ] **Step 4: Create `places.py` with `slugify`**

```python
# backend/app/places.py
"""Place identity + resolution, room-scoped.

Mirrors :mod:`app.roster`'s role for members, and deliberately stays a separate
module and a separate namespace: a place name must never resolve to a person
(design D18). ``roster._NameIndex`` searches **bank account holders**, so this
room's Nhím is reachable as "Trang" while the room eats at "Bún riêu cô Trang".
Keeping the indexes apart is what stops one being answered with the other.
"""
from __future__ import annotations

import re

from app.roster import _fold

def slugify(name: str) -> str:
    """``"Cơm gà Thịnh Lơ"`` -> ``"com-ga-thinh-lo"``.

    Delegates the hard part to :func:`roster._fold`, which already lowercases,
    strips Vietnamese tones, hand-maps ``đ`` (NFD leaves it whole) and squashes
    punctuation to spaces. This only joins the words.
    """
    return re.sub(r"\s+", "-", _fold(name)).strip("-")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_places_resolve.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Verify the schema builds on a fresh and an existing DB**

Run:
```bash
cd backend && source .venv/bin/activate && python -c "
from app.db import Database
d = Database('sqlite:///:memory:'); d.create_all()
from sqlalchemy import inspect
i = inspect(d.engine)
assert 'places' in i.get_table_names(), i.get_table_names()
assert 'place_id' in {c['name'] for c in i.get_columns('meals')}
print('schema ok')"
```
Expected: `schema ok`

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/app/places.py backend/tests/test_places_resolve.py
git commit -m "feat(places): Place model, meals.place_id, slugify"
```

---

### Task 2: The matcher — `_PlaceIndex`, `resolve_one`, `resolve`

**Files:**
- Modify: `backend/app/places.py`
- Test: `backend/tests/test_places_resolve.py`

**Interfaces:**
- Consumes: `places.slugify` (Task 1).
- Produces:
  - `places.list_places(session, room_id, *, include_inactive=False) -> list[Place]`
  - `places.resolve_one(session, room_id, text) -> tuple[Place | None, str]` — tier is one of `"exact"`, `"folded"`, `"prefix"`, `"tokens"`, `"ambiguous"`, `"none"`. `CONFIDENT_TIERS = ("exact", "folded", "prefix")`.
  - `places.resolve(session, room_id, *, names) -> dict` with keys `matched` (`[{"id", "name", "slug"}]`), `unresolved` (`[str]`), `ambiguous` (`[{"name", "candidates"}]`) — same shape as `roster.resolve`.

**Why a separate class from `_NameIndex`:** `_NameIndex` carries member-specific behaviour — a given-name priority tier, the bank-account-holder field, kinship-term stripping — that means nothing for restaurants. Making one class serve both needs field-extractor injection and tier configuration, which is more machinery than the ~40 lines of duplication costs. The *folding* is shared (`roster._fold`), and that is the part that must never diverge.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_places_resolve.py — append

import pytest
from app import places
from app.db import Database
from app.models import Place, Room


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    return d


def _seed(db, rows):
    with db.session() as s:
        s.add(Room(id=1, name="test", invite_token="t"))
        for name, aliases in rows:
            s.add(Place(room_id=1, slug=places.slugify(name), name=name, aliases=aliases))
    return 1


def test_exact_and_folded_tiers(db):
    _seed(db, [("Cơm gà đảo, cơm rang Thịnh Lơ", ["thịnh lơ", "thinh lo"])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "Thịnh Lơ")
        assert (p.slug, tier) == ("com-ga-dao-com-rang-thinh-lo", "exact")
        # tone-free, as people actually type it
        p, tier = places.resolve_one(s, 1, "thinh lo")
        assert (p.slug, tier) == ("com-ga-dao-com-rang-thinh-lo", "exact")


def test_place_prefix_is_stripped_as_a_fallback(db):
    _seed(db, [("Quán Bé Bự - Khoai Tây", ["bé bự"])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "quán bé bự")
        assert p is not None and tier in ("folded", "prefix")


def test_multi_word_matches_tokens_in_any_order(db):
    _seed(db, [("Bún chả rửa xe Nam Đồng", [])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "rửa xe bún chả")
        assert p is not None and tier == "tokens"


def test_two_places_sharing_a_token_stay_ambiguous(db):
    _seed(db, [("Bánh cuốn Bà Hoành", []), ("Bánh cuốn Bà Xuân", [])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "bánh cuốn")
        assert p is None and tier == "ambiguous"


def test_exact_hit_is_never_widened_into_a_token_sweep(db):
    # "Bún mọc" is an exact name AND a token-subset of the other. The narrow
    # tier must win outright, or a lucky substring papers over a real match.
    _seed(db, [("Bún mọc", []), ("Bún mọc Hàng Lược", [])])
    with db.session() as s:
        p, tier = places.resolve_one(s, 1, "bún mọc")
        assert (p.slug, tier) == ("bun-moc", "exact")


def test_unknown_text_resolves_to_nothing(db):
    _seed(db, [("Phở Vui", [])])
    with db.session() as s:
        assert places.resolve_one(s, 1, "sushi") == (None, "none")


def test_resolve_returns_roster_shaped_dict(db):
    _seed(db, [("Phở Vui", ["vui"]), ("Bánh cuốn Bà Hoành", []), ("Bánh cuốn Bà Xuân", [])])
    with db.session() as s:
        res = places.resolve(s, 1, names=["vui", "sushi", "bánh cuốn"])
    assert [m["slug"] for m in res["matched"]] == ["pho-vui"]
    assert res["unresolved"] == ["sushi"]
    assert res["ambiguous"][0]["name"] == "bánh cuốn"
    assert len(res["ambiguous"][0]["candidates"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_places_resolve.py -v`
Expected: FAIL — `AttributeError: module 'app.places' has no attribute 'resolve_one'`

- [ ] **Step 3: Implement the index and resolvers**

```python
# backend/app/places.py — append

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Place
from app.roster import _tokens

#: Generic words Vietnamese puts in front of a venue name ("quán Bé Bự",
#: "chỗ bún chả"). Stripped only as a *fallback*, after the whole string has
#: failed — same discipline as ``roster._HONORIFICS``.
#:
#: Deliberately excludes "hàng" and "nhà": the room really does eat at "Bún Mọc
#: Hàng Lược" and the listing carries a "Nhà hàng Car Park", so stripping those
#: would eat part of a real name.
_PLACE_PREFIXES = {"quan", "cho", "tiem"}

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
    and the first non-empty one wins.
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
    decides what to do, and for meal linking (§5) the answer is "nothing", since
    a silently wrong link moves money history onto the wrong restaurant.
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
    other (D18).
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_places_resolve.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/places.py backend/tests/test_places_resolve.py
git commit -m "feat(places): tiered fuzzy matcher, separate namespace from roster"
```

---

### Task 3: Seed loader + seed lint

**Files:**
- Create: `backend/app/seed_places.py`
- Test: `backend/tests/test_seed_places.py`

**Interfaces:**
- Consumes: `places.slugify`, `models.Place`.
- Produces:
  - `seed_places.load_file(session, room_id, path) -> dict` returning `{"created": int, "updated": int}`.
  - `seed_places.lint(place_dicts, member_names) -> list[str]` returning human-readable problems (empty = clean).
  - CLI: `python -m app.seed_places <room_id> <file.json> [more.json ...]`.

The seed files already exist: `backend/seeds/places-local.json` (41), `places-nearby.json` (35), `places-online.json` (24), plus `nearby-listing.csv` (raw source) and `observations-local.md` (Phase 3).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_seed_places.py
import json
from pathlib import Path

import pytest

from app import places, seed_places
from app.db import Database
from app.models import Place, Room

SEEDS = Path(__file__).resolve().parents[1] / "seeds"


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    with d.session() as s:
        s.add(Room(id=1, name="test", invite_token="t"))
    return d


def test_loads_and_is_idempotent(db, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps([
        {"name": "Phở Vui", "aliases": ["vui"], "tags": ["phở"], "walkable": True},
    ]), encoding="utf-8")
    with db.session() as s:
        assert seed_places.load_file(s, 1, f) == {"created": 1, "updated": 0}
    with db.session() as s:
        assert seed_places.load_file(s, 1, f) == {"created": 0, "updated": 1}
        assert len(places.list_places(s, 1)) == 1


def test_derives_slug_and_keeps_explicit_one(db, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps([
        {"name": "Phở Vui"},
        {"name": "Quán Bé Bự - Khoai Tây", "slug": "be-bu"},
    ]), encoding="utf-8")
    with db.session() as s:
        seed_places.load_file(s, 1, f)
        assert {p.slug for p in places.list_places(s, 1)} == {"pho-vui", "be-bu"}


def test_real_seed_files_load_clean_with_unique_slugs(db):
    total = 0
    with db.session() as s:
        for name in ("places-local.json", "places-nearby.json", "places-online.json"):
            total += seed_places.load_file(s, 1, SEEDS / name)["created"]
        rows = places.list_places(s, 1)
    assert total == 100, total
    assert len({p.slug for p in rows}) == 100
    assert sum(1 for p in rows if p.walkable) == 76


def test_lint_rejects_a_place_alias_that_is_a_bare_member_name():
    # D18: "anh Linh" in Vietnamese means the person, and this room has a Linh.
    bad = [{"name": "Bún Đậu Anh Linh", "aliases": ["anh linh"]}]
    problems = seed_places.lint(bad, member_names=["Giang", "Linh", "Nhím"])
    assert problems and "anh linh" in problems[0]


def test_lint_allows_the_documented_co_trang_exception():
    ok = [{"name": "Bún riêu cô Trang", "aliases": ["cô trang", "co trang"]}]
    assert seed_places.lint(ok, member_names=["Nhím"]) == []


def test_shipped_seeds_pass_the_lint():
    rows = []
    for name in ("places-local.json", "places-nearby.json", "places-online.json"):
        rows += json.loads((SEEDS / name).read_text(encoding="utf-8"))
    assert seed_places.lint(rows, member_names=["Giang", "Linh", "Nhím"]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_seed_places.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.seed_places'`

- [ ] **Step 3: Implement the loader and lint**

```python
# backend/app/seed_places.py
"""Load curated place seeds into a room, and lint them first.

Run once per room after deploy::

    python -m app.seed_places 7 seeds/places-local.json seeds/places-nearby.json seeds/places-online.json

Idempotent by ``(room_id, slug)``: re-running refreshes the curated fields and
never duplicates a row, so the seed files stay the editable source of truth.
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
    from app.roster import list_members

    room_id, paths = int(argv[1]), [Path(p) for p in argv[2:]]
    rows = [r for p in paths for r in json.loads(p.read_text(encoding="utf-8"))]
    db = get_db()
    with db.session() as s:
        names = [n for m in list_members(s, room_id) for n in (m.display_name, m.nickname, m.account_holder or "")]
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_seed_places.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/seed_places.py backend/tests/test_seed_places.py
git commit -m "feat(places): idempotent seed loader with member-name collision lint"
```

---

### Task 4: `find_places` and `add_place` tools

**Files:**
- Modify: `backend/app/tools.py`
- Test: `backend/tests/test_place_tools.py`

**Interfaces:**
- Consumes: `places.resolve`, `places.list_places`, `places.slugify`.
- Produces: two entries in the dict returned by `build_tools` — `"find_places"` and `"add_place"`.

`add_place` writes **directly**, like `add_member` (D7): a place row is inert until someone eats there. Only *observations* (Phase 3) need a confirm card, because they assert something about a person or a business's quality.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_place_tools.py
import pytest

from app.db import Database
from app.models import Place, Room
from app.tools import ToolContext, build_tools


@pytest.fixture()
def tools():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.add(Place(room_id=1, slug="pho-vui", name="Phở Vui", aliases=["vui"]))
    return build_tools(ToolContext(db=db, room_id=1))


def test_find_places_resolves_a_casual_spelling(tools):
    res = tools["find_places"].execute({"names": ["vui"]})
    assert res["ok"] and [m["slug"] for m in res["matched"]] == ["pho-vui"]


def test_find_places_lists_all_when_asked(tools):
    res = tools["find_places"].execute({"all": True})
    assert res["ok"] and len(res["places"]) == 1


def test_add_place_creates_and_returns_the_slug(tools):
    res = tools["add_place"].execute({"name": "Bún chả Tuấn Hưng", "aliases": ["tuấn hưng"]})
    assert res["ok"] and res["slug"] == "bun-cha-tuan-hung"
    assert tools["find_places"].execute({"names": ["tuấn hưng"]})["matched"][0]["slug"] == "bun-cha-tuan-hung"


def test_add_place_is_idempotent_on_slug(tools):
    tools["add_place"].execute({"name": "Phở Vui"})
    res = tools["find_places"].execute({"all": True})
    assert len(res["places"]) == 1, "re-adding an existing place must not duplicate it"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_place_tools.py -v`
Expected: FAIL — `KeyError: 'find_places'`

- [ ] **Step 3: Add the executors inside `build_tools`**

Insert next to `add_member` in `backend/app/tools.py`:

```python
    def find_places(args, _tool_ctx=None) -> dict:
        args = args or {}
        from app import places as places_mod

        with db.session() as s:
            if args.get("all"):
                rows = places_mod.list_places(s, ctx.room_id)
                return {"ok": True, "places": [
                    {"id": p.id, "name": p.name, "slug": p.slug, "tags": p.tags,
                     "walkable": p.walkable} for p in rows
                ]}
            res = places_mod.resolve(s, ctx.room_id, names=[str(n) for n in args.get("names") or []])
        return {"ok": True, **res}

    def add_place(args, _tool_ctx=None) -> dict:
        args = args or {}
        from sqlalchemy import select as _select

        from app import places as places_mod
        from app.models import Place

        name = (args.get("name") or "").strip()
        if not name:
            return _err("Missing place name.")
        slug = places_mod.slugify(name)
        with db.session() as s:
            existing = s.scalars(
                _select(Place).where(Place.room_id == ctx.room_id, Place.slug == slug)
            ).first()
            if existing is not None:
                return {"ok": True, "place_id": existing.id, "slug": existing.slug,
                        "name": existing.name, "already_existed": True}
            p = Place(
                room_id=ctx.room_id, slug=slug, name=name,
                aliases=[str(a) for a in args.get("aliases") or []],
                tags=[str(t) for t in args.get("tags") or []],
                delivery=[str(d) for d in args.get("delivery") or []],
                address=args.get("address"), phone=args.get("phone"),
                walkable=bool(args.get("walkable", True)),
            )
            s.add(p)
            s.flush()
            return {"ok": True, "place_id": p.id, "slug": p.slug, "name": p.name,
                    "already_existed": False}
```

Add the schemas near the other `_*_SCHEMA` constants:

```python
_FIND_PLACES_SCHEMA = {
    "type": "object",
    "properties": {
        "names": {"type": "array", "items": {"type": "string"},
                  "description": "Place names as the user wrote them ('thịnh lơ', 'quán bé bự')."},
        "all": {"type": "boolean", "description": "Return every place in the room."},
    },
}

_ADD_PLACE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"},
                    "description": "Other spellings the group uses, including tone-free forms."},
        "tags": {"type": "array", "items": {"type": "string"}},
        "delivery": {"type": "array", "items": {"type": "string"},
                     "description": "Ordering apps, e.g. ['shopeefood', 'grab']."},
        "address": {"type": "string"},
        "phone": {"type": "string"},
        "walkable": {"type": "boolean", "description": "Can the group walk there from the office?"},
    },
    "required": ["name"],
}
```

Register both in the dict `build_tools` returns:

```python
        "find_places": CustomTool(
            execute=find_places,
            description=(
                "Look up restaurants the group knows by name ('thịnh lơ', 'quán bé bự'), "
                "or list them all with all:true. Returns places, never people."
            ),
            input_schema=_FIND_PLACES_SCHEMA,
        ),
        "add_place": CustomTool(
            execute=add_place,
            description=(
                "Add a restaurant the group has started going to. Writes immediately "
                "(a place row is inert until someone eats there). Seed `aliases` with "
                "every spelling the group actually types."
            ),
            input_schema=_ADD_PLACE_SCHEMA,
        ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_place_tools.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Verify the manifest still builds**

Run:
```bash
cd backend && source .venv/bin/activate && python -c "
from app.tools import tool_manifest
names = [t['name'] for t in tool_manifest()]
assert 'find_places' in names and 'add_place' in names, names
print(len(names), 'tools')"
```
Expected: `16 tools`

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools.py backend/tests/test_place_tools.py
git commit -m "feat(places): find_places and add_place tools"
```

---

### Task 5: Link a meal to its place, non-blocking

**Files:**
- Modify: `backend/app/ledger.py` (`record_meal`), `backend/app/drafts.py` (`_EDITABLE`, commit call), `backend/app/tools.py` (`propose_meal`)
- Test: `backend/tests/test_meal_place_link.py`

**Interfaces:**
- Consumes: `places.resolve_one`, `places.CONFIDENT_TIERS`.
- Produces:
  - `ledger.record_meal(..., place_id: int | None = None)` — persists `Meal.place_id`, and includes `"place_id"` in its returned dict.
  - `propose_meal`'s result dict gains `"place_id": int | None` and `"place_guess": {"id", "name"} | None`.
  - `drafts._EDITABLE` gains `"place_id"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_meal_place_link.py
import pytest

from app import drafts, ledger
from app.db import Database
from app.models import Member, Place, Room
from app.tools import ToolContext, build_tools


@pytest.fixture()
def env():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.add(Member(id=1, room_id=1, display_name="An", nickname="an"))
        s.add(Member(id=2, room_id=1, display_name="Bình", nickname="binh"))
        s.add(Place(room_id=1, slug="bun-cha-rua-xe", name="Bún chả rửa xe Nam Đồng",
                    aliases=["bún chả rửa xe", "rửa xe"]))
        s.add(Place(room_id=1, slug="banh-cuon-ba-hoanh", name="Bánh cuốn Bà Hoành"))
        s.add(Place(room_id=1, slug="banh-cuon-ba-xuan", name="Bánh cuốn Bà Xuân"))
    return db, build_tools(ToolContext(db=db, room_id=1, sender_member_id=1))


def test_confident_dish_links_the_place(env):
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 1, "dish": "bún chả rửa xe"})
    assert res["ok"] and res["place_id"] is not None
    assert res["place_guess"]["name"] == "Bún chả rửa xe Nam Đồng"


def test_ambiguous_dish_leaves_place_null_but_still_proposes(env):
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 1, "dish": "bánh cuốn"})
    assert res["ok"], "an ambiguous place must never block a meal (D2)"
    assert res["place_id"] is None
    assert res["dish"] == "bánh cuốn", "the raw text is kept verbatim"


def test_unknown_dish_never_blocks_the_meal(env):
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 1, "dish": "sushi bar"})
    assert res["ok"] and res["place_id"] is None
    assert res["shares_preview"], "the split is unaffected by place resolution"


def test_meal_with_no_dish_at_all_is_fine(env):
    _db, tools = env
    res = tools["propose_meal"].execute({"participants": [1, 2], "total": 100000, "payer": 1})
    assert res["ok"] and res["place_id"] is None


def test_record_meal_persists_place_id(env):
    db, _tools = env
    with db.session() as s:
        place = s.query(Place).filter_by(slug="bun-cha-rua-xe").one()
        res = ledger.record_meal(
            s, room_id=1, payer_member_id=1, participants=[1, 2],
            total_amount=100000, dish="bún chả rửa xe", place_id=place.id)
        assert res["place_id"] == place.id
        from app.models import Meal
        assert s.get(Meal, res["meal_id"]).place_id == place.id


def test_place_id_is_editable_on_the_draft_card(env):
    assert "place_id" in drafts._EDITABLE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_meal_place_link.py -v`
Expected: FAIL — `KeyError: 'place_id'` from `propose_meal`'s result

- [ ] **Step 3: Thread `place_id` through `record_meal`**

In `backend/app/ledger.py`, add the parameter to `record_meal`'s signature after `dish`:

```python
    dish: str | None = None,
    place_id: int | None = None,
```

Pass it into the `Meal(...)` construction (after `dish=dish,`):

```python
        place_id=place_id,
```

And add it to the returned dict alongside `meal_id`:

```python
        "place_id": place_id,
```

- [ ] **Step 4: Resolve the place in `propose_meal`**

In `backend/app/tools.py`, immediately before `propose_meal`'s `return {...}`:

```python
        # Place resolution is metadata and must NEVER block the bill (D2): this
        # is the deliberate opposite of the _dropped_names guard above, because a
        # missing eater bills everyone wrong while a missing place tag only costs
        # a statistic. Only confident tiers link; a guess rides the card instead,
        # where one tap fixes it (D3).
        place_id = None
        place_guess = None
        dish_text = (args.get("dish") or "").strip()
        if dish_text:
            from app import places as places_mod
            with db.session() as s:
                hit, tier = places_mod.resolve_one(s, ctx.room_id, dish_text)
                if hit is not None:
                    place_guess = {"id": hit.id, "name": hit.name}
                    if tier in places_mod.CONFIDENT_TIERS:
                        place_id = hit.id
```

Add both keys to the returned dict, next to `"dish"`:

```python
            "place_id": place_id,
            "place_guess": place_guess,
```

- [ ] **Step 5: Make the card carry it through to the ledger**

In `backend/app/drafts.py`, add `"place_id"` to `_EDITABLE`:

```python
_EDITABLE = {
    "payer_member_id", "member_participants", "guests", "bill_total",
    "adjustments", "items", "discount_split", "dish", "initiator", "note",
    "place_id",
}
```

And pass it at the `record_meal` call in `commit_draft` (`drafts.py:211`), after `dish=att.get("dish"),`:

```python
        place_id=att.get("place_id"),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_meal_place_link.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the whole suite — nothing about money may change**

Run: `cd backend && source .venv/bin/activate && pytest -q`
Expected: all pass. If a draft or chat test fails, the cause is a changed payload shape, not arithmetic — `place_id`/`place_guess` are additive keys.

- [ ] **Step 8: Commit**

```bash
git add backend/app/ledger.py backend/app/drafts.py backend/app/tools.py backend/tests/test_meal_place_link.py
git commit -m "feat(places): link meals to places on confident match, never blocking"
```

---

### Task 6: Backfill historical meals

**Files:**
- Modify: `backend/app/places.py`
- Test: `backend/tests/test_place_backfill.py`

**Interfaces:**
- Consumes: `places.resolve_one`, `places.CONFIDENT_TIERS`.
- Produces: `places.backfill_links(session, room_id) -> dict` returning `{"linked": int, "skipped": int, "ambiguous": int}`.

Without this, "tuần này ăn bún mấy lần rồi" has nothing to count until months of new meals accumulate — the whole point of Phase 1 is making the *existing* ledger countable.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_place_backfill.py
from datetime import date

import pytest

from app import places
from app.db import Database
from app.models import Meal, Member, Place, Room


@pytest.fixture()
def db():
    d = Database("sqlite:///:memory:")
    d.create_all()
    with d.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.add(Member(id=1, room_id=1, display_name="An", nickname="an"))
        s.add(Place(room_id=1, slug="bun-cha-rua-xe", name="Bún chả rửa xe Nam Đồng",
                    aliases=["bún chả rửa xe", "rửa xe"]))
        s.add(Place(room_id=1, slug="banh-cuon-ba-hoanh", name="Bánh cuốn Bà Hoành"))
        s.add(Place(room_id=1, slug="banh-cuon-ba-xuan", name="Bánh cuốn Bà Xuân"))
    return d


def _meal(s, dish, **kw):
    m = Meal(room_id=1, occurred_on=date(2026, 8, 1), payer_member_id=1,
             total_amount=100000, dish=dish, **kw)
    s.add(m)
    s.flush()
    return m


def test_links_confident_matches_only(db):
    with db.session() as s:
        confident = _meal(s, "bún chả rửa xe")
        ambiguous = _meal(s, "bánh cuốn")
        unknown = _meal(s, "sushi bar")
        res = places.backfill_links(s, 1)
        assert res == {"linked": 1, "skipped": 1, "ambiguous": 1}
        assert s.get(Meal, confident.id).place_id is not None
        assert s.get(Meal, ambiguous.id).place_id is None
        assert s.get(Meal, unknown.id).place_id is None


def test_refuses_the_token_tier(db):
    # "rửa xe bún chả" only matches by token-subset. No card, no human review —
    # a silent wrong link moves money history onto the wrong restaurant.
    with db.session() as s:
        m = _meal(s, "rửa xe bún chả nam đồng hôm qua")
        places.backfill_links(s, 1)
        assert s.get(Meal, m.id).place_id is None


def test_is_idempotent_and_never_relinks(db):
    with db.session() as s:
        _meal(s, "bún chả rửa xe")
        first = places.backfill_links(s, 1)
        second = places.backfill_links(s, 1)
        assert first["linked"] == 1
        assert second["linked"] == 0, "an already-linked meal must be left alone"


def test_skips_voided_and_dishless_meals(db):
    with db.session() as s:
        voided = _meal(s, "bún chả rửa xe", voided=True)
        _meal(s, None)
        res = places.backfill_links(s, 1)
        assert res["linked"] == 0
        assert s.get(Meal, voided.id).place_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_place_backfill.py -v`
Expected: FAIL — `AttributeError: module 'app.places' has no attribute 'backfill_links'`

- [ ] **Step 3: Implement the backfill**

```python
# backend/app/places.py — append

def backfill_links(session: Session, room_id: int) -> dict:
    """Link historical meals to places by resolving ``meals.dish``.

    **Confident tiers only.** A backfill has no draft card, so no human reviews
    the guess before it is written — and a wrong link silently moves a meal's
    money history onto another restaurant, which no later correction will catch
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
```

Add the logger import at the top of `places.py`:

```python
import logging

logger = logging.getLogger("chiatienan")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_place_backfill.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Call the backfill from the seed CLI**

In `backend/app/seed_places.py`, inside `main`, after the load loop and before the final print:

```python
        from app.places import backfill_links
        counts = backfill_links(s, room_id)
        print(f"backfill: {counts}")
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/places.py backend/app/seed_places.py backend/tests/test_place_backfill.py
git commit -m "feat(places): confident-only backfill of historical meal links"
```

---

### Task 7: Lock the place/person namespace boundary (D18)

**Files:**
- Test: `backend/tests/test_name_space_separation.py`

**Interfaces:**
- Consumes: everything above. Adds no production code — this task exists to prove a property that spans two modules and would otherwise be re-broken silently.

This is the guard the operator asked for in as many words: *"don't confuse place name with member name."* `roster._NameIndex` searches **bank account holders**, so in the real production room Nhím (`DINH HONG TRANG`) answers to *Trang* — and the room eats at *Bún riêu cô Trang*.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_name_space_separation.py
"""A place name must never resolve to a person, nor a person to a place (D18).

The hazard is concrete, not theoretical: roster._NameIndex indexes bank account
holders, so this room's Nhím (DINH HONG TRANG) is reachable as "Trang", and the
room eats at "Bún riêu cô Trang". If a place name is read as a member, that
member is added to a split as an eater — the failure mode is money.
"""
import pytest

from app import places, roster
from app.db import Database
from app.models import Member, Place, Room
from app.tools import ToolContext, build_tools


@pytest.fixture()
def env():
    db = Database("sqlite:///:memory:")
    db.create_all()
    with db.session() as s:
        s.add(Room(id=1, name="t", invite_token="t"))
        s.add(Member(id=1, room_id=1, display_name="Nhím", nickname="Nhím",
                     account_holder="DINH HONG TRANG"))
        s.add(Member(id=2, room_id=1, display_name="Linh Nguyen", nickname="Linh",
                     account_holder="NGUYEN ANH LINH"))
        s.add(Place(room_id=1, slug="bun-rieu-co-trang", name="Bún riêu cô Trang",
                    aliases=["cô trang", "co trang"]))
        s.add(Place(room_id=1, slug="bun-dau-met-tran-huu-tuoc",
                    name="Bún đậu mẹt Trần Hữu Tước", aliases=["bún đậu mẹt", "bún đậu"]))
    return db, build_tools(ToolContext(db=db, room_id=1, sender_member_id=1))


def test_member_lookup_never_returns_a_place(env):
    db, _tools = env
    with db.session() as s:
        res = roster.resolve(s, 1, names=["bún riêu cô Trang", "bún đậu mẹt"])
    assert res["matched"] == [], f"roster resolved a restaurant: {res['matched']}"


def test_place_lookup_never_returns_a_member(env):
    db, _tools = env
    with db.session() as s:
        assert places.resolve_one(s, 1, "Nhím") == (None, "none")
        assert places.resolve_one(s, 1, "Linh") == (None, "none")


def test_a_place_named_like_a_member_does_not_join_the_split(env):
    """The money case. 'cô Trang' is the venue; Nhím must not become an eater."""
    _db, tools = env
    res = tools["propose_meal"].execute(
        {"participants": [1, 2], "total": 100000, "payer": 2, "dish": "bún riêu cô Trang"})
    assert res["ok"], "a place that looks like a member must not block the meal"
    eaters = {s["member"] for s in res["shares_preview"]}
    assert eaters == {1, 2}, f"split membership changed: {eaters}"
    assert res["place_guess"]["name"] == "Bún riêu cô Trang"


def test_shipped_seeds_contain_no_bare_member_name_alias():
    """Regression lock on the dropped 'anh linh' alias."""
    import json
    from pathlib import Path

    from app.seed_places import lint

    seeds = Path(__file__).resolve().parents[1] / "seeds"
    rows = []
    for name in ("places-local.json", "places-nearby.json", "places-online.json"):
        rows += json.loads((seeds / name).read_text(encoding="utf-8"))
    assert lint(rows, member_names=["Giang", "Linh", "Nhím",
                                    "HOANG MINH GIANG", "NGUYEN ANH LINH",
                                    "DINH HONG TRANG"]) == []
```

- [ ] **Step 2: Run tests to verify they fail or pass honestly**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_name_space_separation.py -v`

Expected: `test_member_lookup_never_returns_a_place` **may already pass** (the indexes are separate by construction) — that is fine and is the point of pinning it. If `test_a_place_named_like_a_member_does_not_join_the_split` fails, it is a real defect in Task 5's wiring, not a test bug: fix `propose_meal`, never the assertion.

- [ ] **Step 3: Fix anything the tests expose**

No production change is expected. If `roster.resolve` *does* match a place name, do **not** widen the place index or add exclusion lists — the correct fix is that the model called the wrong tool, which belongs in Phase 2's skill file. Record the finding in the plan's notes and move on.

- [ ] **Step 4: Run the full suite**

Run: `cd backend && source .venv/bin/activate && pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_name_space_separation.py
git commit -m "test(places): lock the place/person namespace boundary (D18)"
```

---

## Deploying Phase 1

Phase 1 is invisible to users — no new chat behaviour, no UI. It ships the schema, the matcher, and the linked history that Phase 2 ranks on.

1. Merge to `main`; CI builds and the droplet pulls (`deploy/README.md`). `create_all()` adds `places` and `_sync_additive_columns` adds `meals.place_id` on boot.
2. Find the room id: `curl -sS -H "X-Debug-Key: $DEBUG_API_KEY" https://chiatienan.duckdns.org/internal/debug/rooms`
3. Back up before the first seed (it writes `meals.place_id` across history):
   ```bash
   docker compose exec backend python -c "import sqlite3,datetime,os; os.makedirs('/data/backups',exist_ok=True); sqlite3.connect('/data/chiatienan.db').backup(sqlite3.connect(f'/data/backups/backup-{datetime.date.today()}.db'))"
   ```
4. **The seeds must be in the running image.** They ship via `COPY seeds ./seeds`
   (backend/Dockerfile) — added after Phase 1 was written, because the original
   runbook here documented a command that could not work: the image copied only
   `app` and `agent_sidecar`, so `seeds/places-local.json` did not exist inside
   the container. Confirm before seeding:
   ```bash
   docker compose exec backend ls seeds/
   ```
   Empty or missing means the running image predates that change — redeploy first.
5. Seed and backfill in one command:
   ```bash
   docker compose exec backend python -m app.seed_places <ROOM_ID> \
     seeds/places-local.json seeds/places-nearby.json seeds/places-online.json
   ```
   Expected: `total: +100 ~0` then a `backfill: {...}` line. **A non-zero `ambiguous` count is normal** — those meals stay unlinked until an alias is added.
6. Check what linked, and improve aliases from what didn't:
   ```bash
   curl -sS -H "X-Debug-Key: $DEBUG_API_KEY" "https://chiatienan.duckdns.org/internal/debug/tables/meals.csv?room_id=<ROOM_ID>" -o meals.csv
   ```
   Unlinked rows with a real dish name are the alias gaps. Add them to `places-local.json` and re-run step 4 — it is idempotent and the backfill only touches still-unlinked meals.

## Notes for Phase 2

- `resolve_one` returns `(None, "ambiguous")` on a tie. Phase 2 adds `resolve_best`, which applies D15's full tie-break: first prefer places not tagged `chưa-thử`, then the higher meal count. The `chưa-thử` half needs no stats and could land here; the meal-count half needs Phase 2's `stats()`.
- 17 plain-dish queries are ambiguous across the 100 seeded places (`"bánh cuốn"` matches 6, `"nem nướng"` 5). That is expected and is exactly what `resolve_best` is for.
- `Place.price_hint` and `Place.closed_until` are seeded but unused until Phase 2 reads them.
