# Context — renaming a place's `slug`

**Status:** not built. This is a briefing for a fresh session, not a plan. Read it, then write the plan.

**Why it exists:** the knowledge UI (PR #46) made `name` and `aliases` editable and left `slug` immutable, because a rename that recomputes the slug silently detaches every note about the restaurant. That was the right call for that PR. It is not a permanent answer — a room that seeds "Bún chả Rửa Xe" and then learns the place is actually called "Bún chả Hương Liên" is stuck with `bun-cha-rua-xe` as its identity forever.

## What a slug is

`Place.slug` (`backend/app/models.py:78`) — ASCII, tone-stripped, unique per room (`uq_room_place_slug`). Derived once at creation by `places.slugify(name)` (`places.py`), which delegates the hard part to `roster._fold`. It is the room-scoped identity of a restaurant, and **it is used as a foreign key by things that are not the database**.

`places.EDITABLE` (`places.py`) deliberately omits it; `apply_edits` never recomputes it. `PlaceDialog` shows it as a muted mono line reading "mã định danh, không đổi được" (`place-dialog.tsx:141`).

## Every store that holds a slug

This is the part worth having written down — three of the five are not the `places` table, and two of them were only found by grepping rather than by reasoning about the design.

| Where | Form | Notes |
|---|---|---|
| `places.slug` | column | The source. A rename is one `UPDATE`. |
| `{DATA_DIR}/rooms/{id}/observations.md` | `place:<slug>` field 2 of each line | The obvious one. Rewrite under `chat._agent_lock`, **line-preserving** — see `observations.indexed()` and the K6 note in the PR-46 plan; rebuilding the file from `load()` deletes comments and unparsable lines. |
| `room_messages.attachments` JSON, `kind="memo_draft"` | `{"subject": "place:<slug>"}` | **Found by grep, not by design review.** `memos.create` freezes the subject into the attachment (`memos.py:40`) and `memos.commit` reads `att["subject"]` back out (`memos.py:67`). A memo card still pending when the slug changes will, on commit, write a note under the *old* slug — an orphan. The knowledge panel renders those as `⚠️` / `subject_kind: "unknown"`, so they are at least visible, but the migration should rewrite pending (`status == "pending"`) memo attachments too. |
| `seeds/places-*.json` | `"slug"` key, pinned on all 100 rows | **The nastier trap.** `seed_places.load_file` looks a place up by `(room_id, slug)` from the JSON (`seed_places.py:71`), and `_FIELDS` excludes `slug`, so it never rewrites the slug of a row it finds. Rename in the DB without updating the JSON and the next seeder run **creates a duplicate place** carrying the old slug — and `backfill_links` will then start linking meals to whichever one wins the matcher. |
| `seeds/observations-local.md` | `place:<slug>` | Same file format as the live file; `install_observations` appends any `(subject, text)` pair not already present. A stale slug here re-introduces an orphan note on the next seed run. |

Runtime-derived and therefore *not* a problem: `_PlaceIndex` (indexes `slug.replace("-", " ")` for matching), `suggest_lunch`'s `f"place:{p.slug}"`, `knowledge.SubjectIndex`, and every frontend use — all rebuild from the current row on each call.

## Constraints

- **One writer.** `observations.md` is read-modify-write and the agent turn loop is the other writer. The whole migration goes inside `async with chat._agent_lock:`, like every route in the knowledge API.
- **Line-preserving.** Use `observations.indexed()` / `replace_line`, never a rebuild from parsed rows.
- **`line_id` is content-derived** (`sha1` of the rendered line, `observations.py`). Changing the subject changes every affected line's id, so any client holding ids — an open knowledge panel — must refetch. Publish `knowledge:changed`.
- **Duplicate collision is possible.** Two places merging onto one slug would produce byte-identical lines, which share a `line_id` and become unaddressable. The knowledge API already refuses this on POST/PATCH; a migration must dedupe rather than append.
- **Uniqueness.** `uq_room_place_slug` means the target slug must be free in that room. Decide up front whether "rename onto an existing slug" means *error* or *merge two places* — they are different features and merge is much larger (it also has to move `meals.place_id`).

## Options

1. **Rename-in-place migration** (recommended starting scope). New slug must be free. Steps: validate → `UPDATE places.slug` → rewrite `observations.md` subjects → rewrite pending memo attachments → `knowledge:changed` → trail message. Surfaced in `PlaceDialog` behind a confirmation that states what will move. Leaves the seed JSON to the operator, and **says so in the confirmation**, because a silent seed duplicate is worse than a warning.
2. **Same, plus seed-file rewrite.** Only viable for `seeds/*.json` shipped in the repo; a prod droplet's DB can diverge from them. Probably a separate `--fix-slugs` mode on `seed_places.py` rather than something an HTTP route does.
3. **Merge two places.** Strictly bigger: `meals.place_id` reassignment, dedupe of both places' observations, and a decision about which name survives. Do not fold this into (1).

## Traps

- `roster._fold` maps `đ→d` by hand because NFD leaves it whole. Any new slug must go through `places.slugify`, never a hand-rolled normaliser.
- An empty or punctuation-only new name slugifies to `""`. `create_place` already refuses that; the rename path needs the same guard.
- A rename that produces the *same* slug (e.g. "Quán Bé Bự" → "quán bé bự") should be a no-op, not a migration that rewrites the file for nothing.

## How to know it worked

Extend `backend/tests/test_knowledge_api.py`. The load-bearing assertions:

- After a rename, the place's notes still resolve to it (`subject_label` follows) and `note_count` is unchanged.
- A comment and a malformed line in `observations.md` survive the rewrite byte-for-byte.
- A memo draft created *before* the rename and committed *after* it lands on the new slug, not an orphan.
- Renaming onto a slug another place already holds is refused with the other place named.
- Re-running `seed_places.load_file` with the old pinned slug is either handled or loudly reported — pick one and pin it in a test, because the silent-duplicate failure is the one nobody notices for months.
