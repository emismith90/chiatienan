# Renaming a place's `slug` — one identity change, five stores

> **Status: planned, not built.** Written from the briefing plus a read of production (room 3) through the export API on 2026-08-14. The numbers in [What production actually holds](#what-production-actually-holds) are measured, not assumed, and two of them change the design.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** let a room change a place's `slug` without detaching everything filed under it. PR #46 (`2026-08-14-knowledge-memory-ui.md`, decision K4) made `name` and `aliases` editable and froze the slug, because a rename that recomputes the slug silently orphans every note about the restaurant. That was right for that PR and is wrong as a permanent answer: a room that seeded "Bún chả rửa xe Nam Đồng" and later learns the place is "Bún chả Hương Liên" is stuck with `bun-cha-rua-xe` as its identity forever.

**Architecture:** one endpoint, deliberately *not* a field on `PATCH /places/{id}`, that runs a small migration across three live stores under `chat._agent_lock` — the `places` row, `observations.md`, and pending `memo_draft` attachments — and leaves a `former_slugs` breadcrumb on the row so the two *offline* stores (the seed JSON and the seed observations file) stop being able to resurrect the old identity behind the operator's back.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite/WAL), pytest; Next.js 15 App Router + Tailwind v4 + vitest/jsdom.

**Spec:** [`docs/superpowers/specs/2026-08-14-lunch-suggestion-memory-design.md`](../specs/2026-08-14-lunch-suggestion-memory-design.md) — D4 (two line types, one file) and D18 (places and people are separate namespaces) are the constraints underneath this.

---

## What a slug is

`Place.slug` (`backend/app/models.py:78`) — ASCII, tone-stripped, unique per room (`uq_room_place_slug`). Derived once at creation by `places.slugify(name)` (`places.py:23`), which delegates the hard part to `roster._fold`. It is the room-scoped identity of a restaurant, and **it is used as a foreign key by things that are not the database**.

`places.EDITABLE` (`places.py:55`) deliberately omits it; `apply_edits` never recomputes it. `PlaceDialog` shows it as a muted mono line (`place-dialog.tsx:140`).

## Every store that holds a slug

| Where | Form | Who fixes it today |
|---|---|---|
| `places.slug` | column | one `UPDATE` |
| `{DATA_DIR}/rooms/{id}/observations.md` | `place:<slug>`, field 2 | nobody — the notes orphan |
| `room_messages.attachments`, `kind="memo_draft"` | `{"subject": "place:<slug>"}` | nobody — a pending card commits under the old slug |
| `backend/seeds/places-*.json` | `"slug"` key, pinned on all 100 rows | the operator, by hand, or the next seeder run **creates a duplicate place** |
| `backend/seeds/observations-local.md` | `place:<slug>` | the operator, by hand, or the next seeder run **re-adds an orphan note** |

The first three are live and this endpoint rewrites them. The last two are files in the repo that a droplet's DB has already diverged from; an HTTP route must not write them, so they are handled by making the *reader* tolerant instead (decision S6).

Runtime-derived and therefore not a problem: `_PlaceIndex` (indexes `slug.replace("-", " ")`), `suggest_lunch`'s `f"place:{p.slug}"`, `knowledge.SubjectIndex`, and every frontend use — all rebuild from the current row on each call.

## What production actually holds

Read from room 3 (`12B +2 🐔`, the only real room: 7 members, 337 messages) on 2026-08-14 via the export API:

- **100 places, byte-identical in slug to `backend/seeds/*.json`.** Zero drift in either direction. The seed files *are* the production place list, which makes the duplicate-on-reseed trap not a hypothetical: it is guaranteed to fire the first time anyone renames in the DB and then reseeds.
- **12 of those 100 already have a curated slug that is not `slugify(name)`** — `be-bu` for "Quán Bé Bự - Khoai Tây", `com-ga-thinh-lo` for "Cơm gà đảo, cơm rang Thịnh Lơ", `bun-cha-huong-lien` for "Bún chả Hương Liên (Obama)". The slug is *already* deliberately decoupled from the name. **This kills the obvious design** where renaming a place recomputes its slug: it would turn `be-bu` into `quan-be-bu-khoai-tay` and undo curation nobody asked to undo. See S1.
- **Two `memo_draft` messages have ever existed, and one is still `pending`** — `{"subject": "place:bun-rieu-co-trang", "when": "2026-08-14", "text": "Gọi giá tính tiền"}`. The frozen-subject trap has a live instance sitting in production right now, so the pending-memo rewrite is not defensive coding.
- **42 observation lines, all standing rules.** See [`2026-08-14-stale-observations-measured.md`](../notes/2026-08-14-stale-observations-measured.md).

## Decisions

- **S1 — The new slug is typed, not derived from the new name.** 12/100 production rows have a hand-curated slug shorter than their name; deriving would clobber them. The endpoint takes an explicit `slug` string and normalises it through `places.slugify` — never a second normaliser (trap: `roster._fold` maps `đ→d` by hand because NFD leaves it whole, and any reimplementation gets that wrong). The UI pre-fills the field with the *current* slug, not with a client-side guess at the new one, because that guess would need `_fold` in TypeScript.
- **S2 — Rename, never merge.** The target slug must be free in the room. Renaming onto a slug another place holds is a `409` naming that place. Merging two places is a strictly larger feature — it has to reassign `meals.place_id`, dedupe both places' observations, and decide which name survives — and folding it in here would make the small change unshippable. Non-goal, stated below.
- **S3 — A rename to the same slug is a no-op.** After normalisation, `new == old` returns `{"changed": false}`, writes nothing, and posts no trail. "Quán Bé Bự" → "quán bé bự" must not rewrite the memory file for nothing.
- **S4 — Its own endpoint, not a `PATCH /places/{id}` field.** `POST /places/{id}/slug`. This is a migration with effects in three stores, not a field edit; keeping it off `PlacePatchIn` means `places.EDITABLE` stays literally true and no existing client can rename by accident by round-tripping a form.
- **S5 — One lock, one session, DB first and files last.** Everything runs inside `async with chat._agent_lock:` and one `db.session()`. The `UPDATE` is flushed first and the file rewrite happens after, so a failure in the rewrite rolls the DB back and leaves both stores on the old slug — the only failure state where nothing is orphaned. (The reverse order leaves the file pointing at a slug the DB never adopted.)
- **S6 — `Place.former_slugs`, a JSON list, is what makes the offline stores safe.** Appended on every rename, never hand-editable (not in `EDITABLE`). Three readers consult it:
  - `seed_places.load_file` looks up `(room_id, slug)` and then `(room_id, slug ∈ former_slugs)`, so a seed row still pinned to the old slug **updates the renamed place instead of creating a duplicate**. This is the one trap the briefing flagged as "the nastier trap", and it is the only fix that does not depend on an operator remembering a JSON file.
  - `seed_places.install_observations` canonicalises each seed line's `place:` subject through the same index before its "already present?" compare, so a stale seed slug cannot re-add an orphan note.
  - `knowledge.SubjectIndex` resolves a former slug to the place and reports the *current* slug as `subject_key`, so a line that escaped the rewrite (a hand edit, a restored backup) still renders under the right restaurant instead of `⚠️ unknown`.

  A new column is close to free here: `db._sync_additive_columns` ALTERs it in on startup (`db.py:64`), so there is no migration step to forget.
- **S7 — Only `pending` memo drafts are rewritten.** A `committed` memo is the historical record of a note that was already written under the old slug (and whose observation line this same rename is moving); rewriting its subject would falsify the record. A `cancelled` one will never be applied. Both are left alone.
- **S8 — The rewrite dedupes rather than appends.** `line_id` is `sha1` of the rendered line, so two byte-identical lines share an id and become unaddressable — the knowledge API already refuses to create that on POST and PATCH. A rewrite can produce it when the file already holds a line under the target subject with the same date, gate and text. Such a line is **dropped, not written**, and the count is reported. As a side effect this repairs a pre-existing duplicate pair under the old subject.
- **S9 — Line-preserving, one read and one write.** `observations.retarget_subject` works over the raw line list from `_read_raw`, keyed by the indices `indexed()` reports, and writes once. Not N calls to `replace_line` (N re-reads and N rewrites of the whole file), and never a rebuild from `load()` — that is the K6 bug PR #46 fixed, and it eats comments and unparsable lines.
- **S10 — `line_id`s all change, so the panel must refetch.** Changing the subject changes every affected line's content hash. The route publishes `knowledge:changed` like every other knowledge write; an open panel holding stale ids reloads.
- **S11 — The confirmation states what moves, in numbers, before it moves.** The dialog shows the note count (already in the snapshot as `note_count`) and the pending-card count (added to `place_rows` by this plan), and names the seed-file caveat explicitly. A silent seed duplicate is worse than a warning nobody needed.
- **S12 — Renaming onto another place's *former* slug is refused.** Allowing it would make one slug resolve to two rows in `SubjectIndex` and in `seed_places.load_file`, which is the ambiguity `resolve_one` exists to refuse. `409`, naming the other place.

## API surface

```
POST /api/rooms/{room_id}/places/{place_id}/slug     { "slug": "bun-cha-huong-lien" }
  200 { ok, changed, slug, former_slug, notes_moved, notes_deduped, memos_moved }
  404 no such place in this room
  409 slug is taken / was previously used by another place        [S2, S12]
  422 empty or unusable slug                                       [S1]
```

`GET /api/rooms/{room_id}/knowledge` gains `places[].former_slugs` and `places[].pending_memo_count`.

## File Structure

Backend:
- `backend/app/models.py` — **modify**: `Place.former_slugs` (JSON, default `[]`).
- `backend/app/places.py` — **modify**: `rename_slug(session, room_id, place_id, new_slug) -> dict`; `former_slugs` stays out of `EDITABLE`, and the module comment above it changes from "never will be" to "only through `rename_slug`".
- `backend/app/observations.py` — **modify**: `retarget_subject(room_id, *, old, new) -> dict`; `_write_raw` via temp file + `os.replace`.
- `backend/app/memos.py` — **modify**: `retarget_subject(session, room_id, *, old, new, new_label) -> int`.
- `backend/app/knowledge.py` — **modify**: `SubjectIndex` indexes former slugs; `place_rows` reports `former_slugs` + `pending_memo_count`.
- `backend/app/seed_places.py` — **modify**: `load_file` falls back to `former_slugs`; `install_observations` canonicalises subjects.
- `backend/app/main.py` — **modify**: the route + `PlaceSlugIn`.
- `backend/tests/` — **modify**: `test_knowledge_api.py`, `test_observations_edit.py`, `test_seed_places.py`.

Frontend:
- `frontend/src/lib/api.ts` — **modify**: `renamePlaceSlug`, `KnowledgePlace.former_slugs` / `.pending_memo_count`.
- `frontend/src/components/chat/place-dialog.tsx` — **modify**: the identity line gains a rename disclosure.
- `frontend/src/components/chat/__tests__/place-dialog.test.tsx` — **modify**.

---

## Phase 1 — The rename, end to end in the backend

### Task 1: `former_slugs` and the retarget primitives

**Files:** modify `backend/app/models.py`, `backend/app/observations.py`, `backend/app/memos.py`; test `backend/tests/test_observations_edit.py`, `backend/tests/test_memos.py`.

**Interfaces:**
- `observations.retarget_subject(room_id, *, old: str, new: str) -> {"moved": int, "deduped": int}`
- `memos.retarget_subject(session, room_id, *, old: str, new: str, new_label: str) -> int`

- [ ] Failing test: a file holding a comment, a malformed line, two lines under `place:old` and one under `member:x` comes back with the two lines retargeted and **the comment and the malformed line byte-identical and in position**; lines under other subjects are untouched; the return counts what moved.
- [ ] Failing test: when the file already holds a line identical to what a moved line would render as, the moved line is dropped and `deduped` is 1 — never two lines sharing a `line_id`.
- [ ] Failing test: two byte-identical lines already under `place:old` collapse to one on retarget (a pre-existing unaddressable pair is repaired, not carried forward).
- [ ] Failing test: `memos.retarget_subject` rewrites `subject` and `subject_label` on `pending` drafts only; `committed` and `cancelled` attachments come back untouched (S7).
- [ ] Implement. `retarget_subject` reads once and writes once (S9). `Place.former_slugs` is added as a JSON column with `default=list`; confirm `db._sync_additive_columns` ALTERs it into an existing SQLite file rather than needing a hand migration.
- [ ] Make `observations._write_raw` write to a sibling temp file and `os.replace` it. Every writer benefits, and this one rewrites the whole file.
- [ ] Commit.

### Task 2: `places.rename_slug`

**Files:** modify `backend/app/places.py`; test `backend/tests/test_places_resolve.py` (or a new `test_place_rename.py`).

**Interfaces:** `places.rename_slug(session, room_id, place_id, raw_slug) -> dict` — raises `PlaceError` on every refusal.

- [ ] Failing tests, one per refusal: an empty or punctuation-only slug is refused (S1, same guard `create_place` already has); a slug another place holds is refused *naming that place* (S2); a slug in another place's `former_slugs` is refused naming it (S12); a normalised slug equal to the current one returns `changed: False` and writes nothing at all — assert the observations file's mtime and bytes are unchanged (S3).
- [ ] Failing test: a successful rename sets `slug`, appends the old slug to `former_slugs`, and — renaming *back* to a slug the place previously held — removes it from `former_slugs` rather than leaving the row claiming both.
- [ ] Failing test: the input goes through `places.slugify`, so `" Bún Chả Hương Liên "` and `"bun-cha-huong-lien"` are the same request (S1).
- [ ] Implement. Order: validate → `UPDATE` + flush → `observations.retarget_subject` → `memos.retarget_subject` → return the counts (S5). `observations` and `memos` are imported inside the function; `memos` imports `chat`, and a module-level import here would risk a cycle.
- [ ] Commit.

### Task 3: The route

**Files:** modify `backend/app/main.py`; test `backend/tests/test_knowledge_api.py`.

- [ ] Failing test: `POST .../places/{id}/slug` renames and returns the counts; a place from another room is a 404 (`_check_room` plus the room check `patch_place_route` already does); a collision is a 409 carrying the other place's name; an empty slug is a 422.
- [ ] Failing test — **the one the briefing calls load-bearing**: a place with three notes is renamed, and afterwards `GET /knowledge` shows the same `note_count`, the same three rows, and `subject_label` still resolving to the place. Nothing orphaned, nothing lost.
- [ ] Failing test: a `memo_draft` created *before* the rename and committed *after* it writes its line under the **new** slug, and that line shows up attached to the place — not as an `⚠️ unknown` orphan. Model it on the live production draft: `place:bun-rieu-co-trang`, dated, no gate.
- [ ] Failing test: the write takes `chat._agent_lock` and publishes `knowledge:changed` (S10), and posts exactly one trail message naming both slugs and the counts.
- [ ] Implement. Trail copy (English, per the app-wide language pass): `📓 {name} changed the identifier for "{place}": {old} → {new} (3 notes, 1 pending card).` No money figure, ever (D3).
- [ ] Commit.

### Task 4: The offline stores stop resurrecting the old slug

**Files:** modify `backend/app/seed_places.py`, `backend/app/knowledge.py`; test `backend/tests/test_seed_places.py`, `backend/tests/test_knowledge_api.py`.

- [ ] Failing test — **the silent-duplicate failure, pinned**: seed a room from a JSON row pinned to `slug: "bun-cha-rua-xe"`, rename the place to `bun-cha-huong-lien`, re-run `load_file` with the *unchanged* JSON, and assert the room still has **one** place — the renamed one, with its curated fields refreshed — and no row carrying the old slug. This is the assertion nobody notices is missing for months.
- [ ] Failing test: `install_observations` re-run with a seed line still reading `place:bun-cha-rua-xe`, whose text is already in the room's file under the new slug, adds **nothing** (`{"added": 0, "skipped": 1}`).
- [ ] Failing test: an observation line left under a former slug resolves through `SubjectIndex` to the place, with `subject_kind: "place"` and `subject_key` reporting the *current* slug, so the panel groups it under the restaurant rather than showing `⚠️`.
- [ ] Implement. `load_file`'s fallback lookup is by `former_slugs` containment; on SQLite, load the room's places once and match in Python rather than reaching for a JSON operator.
- [ ] Add `former_slugs` and `pending_memo_count` to `place_rows` (one pass over the room's pending `memo_draft` rows into a `Counter`, not a query per place).
- [ ] Commit.

## Phase 2 — The affordance

### Task 5: Renaming from `PlaceDialog`

**Files:** modify `frontend/src/lib/api.ts`, `frontend/src/components/chat/place-dialog.tsx`; test `frontend/src/components/chat/__tests__/place-dialog.test.tsx`.

- [ ] Failing test: the identity line renders the slug plus a `Change` control; opening it reveals an input pre-filled with the current slug and a confirmation that names the counts ("3 notes and 1 pending card will move to the new identifier") and the seed caveat; the rename only fires on the explicit confirm, not on the dialog's Save.
- [ ] Failing test: a 409 shows the server's own message (the existing `writeError` path already prefers `detail` — a place 409 is a collision, not a stale etag, so it must not trigger a reload banner); a 422 shows the server's message; success closes the disclosure and the dialog refetches.
- [ ] Implement. No client-side slug normalisation (S1) — the field takes what is typed, the server normalises, and the response carries the slug that was actually applied so the dialog can show it.
- [ ] Commit.

---

## How to know it worked

The briefing's five load-bearing assertions map onto Task 3 and Task 4 above:

| Assertion | Task |
|---|---|
| Notes still resolve to the place; `note_count` unchanged | 3 |
| A comment and a malformed line survive byte-for-byte | 1 |
| A memo drafted before and committed after lands on the new slug | 3 |
| Renaming onto a taken slug is refused, naming the holder | 2, 3 |
| Re-seeding with the old pinned slug is handled, and pinned in a test | 4 |

Beyond the suite, the one thing worth doing by hand before this ships: on the droplet, rename a place that has notes and a pending memo card, then re-run the seeder with the unmodified JSON and confirm the place count stays at 100.

## Risks & non-goals

- **Merging two places is not this.** It needs `meals.place_id` reassignment, a dedupe across both places' observation lines, and a decision about which name and which stats survive. It is a bigger feature with a money-history failure mode, and it deserves its own plan (S2).
- **`former_slugs` grows without bound.** Three renames leave three entries. Nothing prunes it, and nothing should: the whole point is that a seed file pinned to a two-renames-ago slug still finds its row. If a row ever accumulates enough to matter, that is a room with a naming problem, not a storage problem.
- **A line left under a former slug is legible but inert.** `SubjectIndex` will render it under the right place, but `for_subjects` asks for `place:<current-slug>`, so the model does not read it. This can only arise from a hand edit of the file or a restored backup — the rename rewrites the live file and `install_observations` canonicalises — so the fix is to run the rename again rather than to build a sweep. Revisit if it ever actually happens.
- **The seed JSON still holds the old slug after a rename.** By design: an HTTP route must not write repo files, and the droplet's DB has already diverged from them. `former_slugs` makes that safe rather than making it correct, and the dialog says so (S11). Tidying the JSON stays an operator job.
- **No preview.** The dialog states counts but does not list the affected lines. The notes are already visible one tab over, and the operation is reversible by renaming back — `former_slugs` even makes the return trip clean.
