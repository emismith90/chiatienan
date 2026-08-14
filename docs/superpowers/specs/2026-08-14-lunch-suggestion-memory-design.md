# Phoenix answers "trưa nay ăn gì?" — place identity, lunch memory, clock rules

**Date:** 2026-08-14 · **Status:** approved (scope + mechanism confirmed by operator)

## Problem

Phoenix knows money and nothing else. Asked "trưa nay ăn gì?" it has no answer, and
the daily six-person deadlock stays a six-person deadlock.

Three kinds of knowledge are missing, and they behave differently enough that one
store cannot hold all three:

1. **Numbers** — how often the group eats somewhere, on which weekday, at what real
   cost per head. Must be *computed*, never estimated by a model.
2. **Prose** — "Nhím đề xuất rồi lại đổi ý", "quán này hết thịt gà". Not table-shaped;
   schematising it destroys the meaning. **Decays**: a complaint from eight months ago
   is weak evidence today.
3. **Standing rules** — "phải gọi trước 11h30", "đông lúc 12h nên đi sớm". Prose-shaped
   like (2) but they **do not decay**, and they gate whether a suggestion is even valid
   *right now*. The same question has a different right answer at 11:20 and 11:55.

A fourth problem underpins all of them: `meals.dish` is free text. `"bún chả rửa xe"`,
`"Bún chả"` and `"bun cha"` are three strings for one restaurant, so today there is no
identity to attach any of this to and no way to count anything.

## Decisions

- **D1 — Numbers in Python, prose in the model.** `suggest_lunch` ranks candidates and
  computes every count, interval and average server-side; the model receives a decided,
  ordered list and writes prose around it. Same authority rule as `pick_random` and
  `resolve_date`, for the same reason: a model that eyeballs "we ate bún chả 3 times"
  is wrong eventually, and a confidently wrong count poisons trust in everything else
  the bot says.
- **D2 — Place resolution never blocks a meal.** Deliberately the opposite of
  `_dropped_names` (`tools.py:88`), which refuses loudly when a named eater does not
  resolve. That refusal is right — a missing eater bills everyone wrong. A missing
  place tag costs a statistic. Money is the payload, the place is metadata, and
  metadata must never hold up a bill.
- **D3 — The place guess rides the existing draft card.** `_EDITABLE` (`drafts.py:24`)
  already carries `dish`; adding `place_id` means a wrong guess is one tap to fix on the
  card the user is already confirming. No new confirm flow for linking.
- **D4 — Two line types, one file.** Observations and standing rules share a format and
  a file, separated by the `when` field (`YYYY-MM-DD` vs `always`). Recency filtering
  applies only to dated lines, so a standing rule can never be aged out.
- **D5 — Suggestions speak in price bands, not VND.** `suggest_lunch` returns
  `rẻ`/`vừa`/`đắt`, computed in Python from ledger history. No raw amount is ever handed
  to the model in a suggestion context, so a suggestion can never be mistaken for a
  ledger figure and D3-money-safety is sidestepped structurally rather than by
  instruction.
- **D6 — Preferences are observations, not a column.** "Giang thích bún riêu" is a
  `member:` line. An earlier draft of this design gave `Member` a `food_prefs` JSON
  column; it would duplicate the observations file for no query benefit.
- **D7 — Places are inert, observations are assertions.** `add_place` writes directly,
  like `add_member`. `remember`/`forget` go through a confirm card. A place row does
  nothing until someone eats there; an observation is a claim about a person or a
  business's quality, and TODO.md's "no way to verify a false claim" problem applies.
- **D8 — Ledger price beats menu price.** `Place` has no price column. `meals.total_amount
  ÷ heads` is what the group actually paid, after discounts, per person.
- **D9 — Gate vocabulary stays tiny.** Two verbs (`busy@`, `order-by@`). Anything that
  does not fit gets `-` and lives as pure prose the model may mention but Python does
  not gate on. This escape hatch is what stops the feature growing into a rules DSL,
  which is the obvious way it goes bad.

## Design

### §1 Place identity

New model in `models.py`:

```python
class Place(Base):
    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("room_id", "slug", name="uq_room_place_slug"),)

    id: int
    room_id: int          # FK rooms.id, indexed — room-scoped like everything else
    slug: str             # String(60), indexed. Stable identity used in observations.md
    name: str             # String(120), display form with diacritics
    aliases: list         # JSON, default list
    tags: list            # JSON, default list — ["cơm", "gà"], free vocabulary
    delivery: list        # JSON, default list — ["shopeefood", "grab"]
    walk_minutes: int|None
    active: bool          # default True
    created_at: datetime
```

`Meal` gains `place_id: int | None` (FK `places.id`, nullable, indexed).

Both land automatically on deploy: `create_all()` builds the new table and
`_sync_additive_columns` adds the column (`db.py:138`). No hand-written migration.

`slug` is derived from `name` via `roster._fold` with spaces hyphenated
(`"Cơm gà Thịnh Lơ"` → `"com-ga-thinh-lo"`), unless supplied explicitly.

### §2 The matcher — `app/places.py`

`roster._NameIndex` (`roster.py:87`) already solves this problem for member names:
tone-folding, token matching in any order, and tiers running narrow→broad so an exact
hit is never widened into a lucky substring.

`_PlaceIndex` reuses `roster._fold` and `roster._tokens` (module-level and
place-agnostic) but is a **separate ~30-line class**, not a generalisation of
`_NameIndex`. `_NameIndex` carries member-specific behaviour — given-name tier priority,
the bank-account-holder field, kinship-term stripping — that means nothing for
restaurants. Making one class serve both needs field-extractor injection and tier
configuration, which is more machinery than the duplication costs.

Searchable fields: `name`, `slug`, every alias.

Lookup tiers, first non-empty wins:

1. `exact` — case-insensitive raw match
2. `folded` — tone-stripped match
3. `folded` after stripping a leading place word (`_PLACE_PREFIXES = {"quan", "cho",
   "hang", "tiem", "nha hang"}`), mirroring `_strip_honorific`
4. multi-word: every token of the query is one of that place's tokens, any order
5. single word: token match

```python
def resolve_one(session, room_id, text) -> tuple[Place | None, str]:
    """(place, tier) where tier ∈ {"exact","folded","prefix","tokens","none"}.
    Ambiguity (>1 hit) returns (None, "ambiguous")."""

def resolve(session, room_id, *, names) -> dict:
    """{"matched", "unresolved", "ambiguous"} — same shape as roster.resolve."""
```

The tier is returned rather than swallowed because §5 treats confident tiers
(`exact`/`folded`/`prefix`) differently from guesses (`tokens`).

### §3 Ledger-derived stats

`places.stats(session, room_id, *, window_days=120) -> dict[int, PlaceStats]`:

| Field | Derivation |
|---|---|
| `times` | count of non-voided meals with this `place_id` in window |
| `last_on` | max `occurred_on` |
| `days_since` | `today_ict() - last_on`, `None` if never |
| `weekday_counts` | `{0..6: n}` over the window |
| `avg_per_head` | mean of `total_amount ÷ (len(shares) + len(guests))` |
| `band` | `rẻ`/`vừa`/`đắt` — tertiles of `avg_per_head` across the room's places |

`band` is relative to the room, not absolute VND, so it stays correct as prices drift.
Voided meals are excluded throughout (`Meal.voided`).

**Places with no meal history are first-class.** On day one every seeded place has
`times=0`, and a design that only ranked eaten-at places would suggest nothing at all.
Such a place gets `days_since=None`, `band=None`, and sits in the **middle** of the
ranking — never penalised as stale, never boosted as novel. It is suggestible
immediately, and a `budget` filter simply does not exclude it (an unknown band cannot
be ruled out). A place stays in this state until its first linked meal.

### §4 Observations & rules — `app/observations.py`

File: `{DATA_DIR}/rooms/{room_id}/observations.md`, sibling to the existing `memory.md`,
via `memory.room_memory_dir()`. Append-oriented, one line per fact:

```
- 2026-03-03 | place:com-ga-thinh-lo | -              | Làm quá chậm, 1 tiếng mới có món. Tính sai giá.
- 2026-03-05 | member:giang          | -              | Thích ăn bún riêu.
- 2026-03-05 | member:nhim           | -              | Đề xuất quán rồi lại đổi ý phút chót.
- always     | place:com-ga-thinh-lo | order-by@11:30 | Phải đặt trước — gọi điện thoại.
- always     | place:be-bu           | busy@12:00     | Đông lúc 12h, phải đi sớm.
```

Four fields, one `split("|", 3)` after stripping the leading `- `. No parser.

- **`when`** — `YYYY-MM-DD` or `always`. Dated lines are recency-filtered; `always`
  lines never are (D4).
- **`subject`** — `place:<slug>` (joins §1) or `member:<nickname>` (resolved through
  `roster`). This is the join that makes numbers and prose describe the same thing.
- **`gate`** — `-`, `busy@HH:MM`, or `order-by@HH:MM` (D9).
- **`text`** — free Vietnamese prose, untouched.

**Malformed lines are skipped with a log warning, never raised.** This file is
hand-edited by the operator; a stray line must degrade one fact, not break lunch.

API:

```python
load(room_id) -> list[Observation]
append(room_id, obs) -> None
remove(room_id, *, subject, when, text) -> bool      # exact line match
for_subjects(room_id, subjects, *, since_days=180) -> list[Observation]
count_since(room_id, subject, *, since: date) -> int  # "lần thứ 3 tháng này"
gate_status(obs, place, now) -> tuple[str, int|None]  # (status, minutes_left)
```

`count_since` is why the third example in the brief works without the model counting:
Python greps `member:nhim`, counts this month's lines, and hands over `3`.

**Gate evaluation** (all clock arithmetic in Python, against `clock.now_ict()`):

- `busy@HH:MM` — `eta = now + (place.walk_minutes or 0)`. `eta > busy_at` → `too_late`;
  within `_ACT_NOW_BUSY = 15min` → `act_now`; else `ok`. Unseeded `walk_minutes`
  degrades to a plain time compare, so the rule still works, less precisely.
- `order-by@HH:MM` — `now > order_by` → `too_late`; within `_ACT_NOW_ORDER = 20min` →
  `act_now`; else `ok`.

`too_late` on a `busy@` means "sẽ đông/không kịp", not "closed" — the prose carries the
nuance, the status only gates.

### §5 Linking meals to places

In `propose_meal`, after the dish is known:

| `resolve_one` tier | Draft behaviour |
|---|---|
| `exact` / `folded` / `prefix` | `place_id` set; card shows the place name |
| `tokens` / `ambiguous` | `place_id` null, `place_guess` set; card offers it as correctable |
| `none` | `place_id` null; raw `dish` kept verbatim |

Never blocks, never refuses, never asks a follow-up question (D2). `_EDITABLE` gains
`place_id`; `ledger.record_meal` persists it.

When the dish matched nothing, Phoenix **may** propose adding it as a place — but on a
later turn, never on the money card. Cluttering a payment confirm with a memory question
is how people start tapping through without reading.

**Backfill:** `places.backfill_links(session, room_id) -> dict` links historical meals by
resolving `meals.dish`, **confident tiers only** (`exact`/`folded`/`prefix`). There is no
card in a backfill, so no human reviews the guess, and a silent wrong link is worse than
an unlinked meal. Run once after seeding via `python -m app.seed_places`; logs
`{linked, skipped, ambiguous}`.

Without this, "tuần này ăn bún mấy lần rồi" has nothing to count until months of new
meals accumulate.

### §6 Tools

Added to `build_tools` (`tools.py:315`):

| Tool | Args | Returns |
|---|---|---|
| `suggest_lunch` | `budget?`, `mood?`, `exclude?`, `eaters?` | ranked candidates: name, `band`, `days_since`, gate `status` + `minutes_left`, relevant observation lines |
| `find_places` | `query?`, `all?` | `{matched, unresolved, ambiguous}` |
| `add_place` | `name`, `aliases?`, `tags?`, `delivery?`, `walk_minutes?` | created place (direct write, D7) |
| `remember` | `subject`, `text`, `when?`, `gate?` | memo draft card |
| `forget` | `subject`, `text` | memo draft card (`action: "remove"`) |

`suggest_lunch` ranking, entirely in Python: drop `too_late` candidates to the bottom
with their reason; penalise recency (`days_since`); boost weekday affinity; filter by
`band` when `budget` given; exclude anything in `exclude`. Ties broken by
`random.choice` — the tool decides, so the model cannot (mirrors `pick_random`).

### §7 The confirm card

New `RoomMessage.kind = "memo_draft"`, attachments:

```json
{"type": "memo_draft", "action": "add|remove", "subject": "place:be-bu",
 "subject_label": "Bé Bự", "when": "always", "gate": "busy@12:00",
 "text": "Đông lúc 12h…", "status": "pending|committed|cancelled"}
```

New module `app/memos.py` (~80 lines): create / commit / cancel. It reuses the
RoomMessage-plus-attachments **pattern**, not `drafts.py`'s code — `_sync_items`,
`prorate_items` and the itemised-split machinery are money-specific and irrelevant here.
Commit calls `observations.append`/`remove`.

Endpoints mirror the existing draft routes: `POST /api/rooms/{room_id}/memos/{message_id}/commit`
and `/cancel`, `require_session` + `_check_room`.

Frontend: one `MemoCard` component (text + accept/reject, no editable fields) and a
`message-list.tsx:106` branch. This is the only frontend work in the design — §1–§6 are
backend-only and answer in plain chat text, like `pick_random` does today.

### §8 Skill

`backend/app/agent_skills/skills/suggest-lunch/SKILL.md`, Vietnamese, matching the voice
of `pick-random/SKILL.md`. Load-bearing rules:

- `suggest_lunch` decides the order — never re-rank, never pick a different one.
- Never compute counts, intervals, averages or clock arithmetic; the tool supplies them.
- Speak in bands (`rẻ`/`vừa`/`đắt`), never VND (D5).
- Relay gate status in the tool's terms: `act_now` → say what to do now and how long is
  left; `too_late` → say so and offer the next candidate.
- **At most one `remember` proposal per turn**, and only when the user says something
  evaluative about a place or a person — not on every mention. A bot that offers to
  remember something every turn gets muted.
- Never propose a memo on the same turn as a money card.

### §9 Seed formats

`python -m app.seed_places <room_id> <file.json>` — idempotent by `(room_id, slug)`,
updates aliases/tags on re-run, then invokes `backfill_links`.

```json
[
  {
    "name": "Cơm gà Thịnh Lơ",
    "aliases": ["thịnh lơ", "cơm gà thịnh lơ", "thinh lo"],
    "tags": ["cơm", "gà"],
    "delivery": ["shopeefood"],
    "walk_minutes": 5
  }
]
```

`slug` is optional (derived from `name`). Only `name` is required; everything else
defaults empty. **Aliases are what make casual chat resolve** — seed every spelling the
room actually types, including tone-free forms.

Observations seed the file directly, in the §4 format.

## Testing

- `test_places_resolve.py` — each tier; tone-folding (`"thinh lo"` → `"Thịnh Lơ"`);
  `"quán bé bự"` → `"Bé Bự"`; two places sharing a token stay `ambiguous`; exact hit is
  never widened.
- `test_places_stats.py` — `times`/`days_since`/`weekday_counts`/`band` over fixture
  meals; voided meals excluded; guests counted in `avg_per_head`.
- `test_observations.py` — round-trip append/load/remove; malformed line skipped and
  logged, not raised; `always` survives `since_days` filtering while a dated line does
  not; `count_since` returns the number the brief's third example needs.
- `test_gates.py` — frozen clock: `busy@12:00` with and without `walk_minutes` at 11:20 /
  11:50 / 12:10; `order-by@11:30` at 11:00 / 11:25 / 11:40.
- `test_suggest_lunch.py` — yesterday's place is penalised; `budget="rẻ"` filters by band;
  a `too_late` candidate sinks with its reason attached; `exclude` honoured; **a room whose
  places all have zero meals still returns a ranked list** (the day-one case).
- `test_meal_place_link.py` — confident tier links; token tier leaves `place_id` null with
  `place_guess` set; an unresolvable dish never blocks `propose_meal`; `backfill_links`
  refuses the token tier.
- `test_memos.py` — create/commit/cancel; commit appends the exact line; `forget` removes
  it; committing twice is a no-op.
- Frontend: `memo-card.test.tsx` — renders subject + text, accept calls the commit
  endpoint, reject cancels.

## Phasing

1. **Identity & linking** — §1, §2, §5, `find_places`/`add_place`, seed loader, backfill.
   No user-visible feature; makes the history countable.
2. **Suggestion** — §3, `suggest_lunch`, §8 skill. "Trưa nay ăn gì?" works, backend-only,
   plain text answer.
3. **Memory** — §4, §7, `remember`/`forget`, memo card + frontend.

## Out of scope

- **Google Places discovery.** Real key, per-call cost, container egress, caching. It is
  the only piece with an external dependency, and the rarest ask. Hanoi street food is
  thinly covered in Places anyway — the seed list *is* the discovery layer until it
  genuinely runs dry.
- **ShopeeFood / Grab menus.** No public API; scraping is fragile and against ToS. Menus
  are seeded by hand or not at all.
- Per-dish (as opposed to per-place) statistics.
- Opening hours as structured data — `busy@`/`order-by@` cover the asks; a closed-today
  rule is prose until a real case demands a verb (D9).
- Automatic decay or cleanup of old observations. The date is in the line and the query
  filters on it; nothing needs deleting.
