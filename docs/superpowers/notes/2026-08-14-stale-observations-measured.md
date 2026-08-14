# Stale observations: measured, and the answer is still "do nothing"

**Date:** 2026-08-14 · **Outcome: no code.**

The open question was whether a dated observation past `DEFAULT_SINCE_DAYS` (180) should be pruned, swept, promoted to a standing rule, archived, or left flagged-but-present as it is today. The briefing that raised it set a gate before any of that: *look at a real room's `observations.md` and count the lines and how many are stale. If the answer is "nine lines, two stale", the correct outcome is a note saying so and no code.*

This is that note.

## What production holds

Room 3 (`12B +2 🐔`) is the only real room — 7 members, 337 messages, three weeks of daily use. The other three rooms have 9, 0 and 0 messages.

**42 observation lines. All 42 are standing rules. Zero are dated, so zero are stale, and zero can ever become stale.**

## How that was established

`observations.md` is a file, not a table, so the export API cannot serve it directly (`debug_api.py` exposes tables, the conversation, a DB snapshot and logs — nothing under `{DATA_DIR}/rooms/`). It was reconstructed from the three — and only three — writers of that file:

| Writer | Reaches the file via | Count in room 3 |
|---|---|---|
| `seed_places.install_observations` | `python -m app.seed_places` | 42 lines, from `backend/seeds/observations-local.md` — every one `always` |
| `memos.commit` | the memo confirm card | **0**: two `memo_draft` messages have ever existed, one `cancelled` and one still `pending` |
| `POST /api/rooms/{id}/observations` | the knowledge panel | **0**: every such write posts a `📓` trail message, and the room has none |

The seeded set is the whole file: `backend/seeds/observations-local.md` holds 42 lines and not one carries a date. Room 3's place list is also byte-identical to `backend/seeds/*.json` (100 rows, zero drift either way), which confirms the seeder ran and that nothing has been added since.

The room's own UI agrees: the **Notes** tab counts 42.

## What that means

Staleness is structurally impossible in the only room that has any memory at all. `for_subjects` never filters a rule; `knowledge.observation_rows` can only set `stale: true` on a line with a date; there are no dated lines. Every candidate on the option list — a UI sweep, promote-to-rule, an archive file, a timer — is machinery for a problem that has not started.

It is also worth naming *why* the file is all rules: the 42 lines are curated seed knowledge ("phải gọi trước", "hay hết gà đùi", "không có chỗ ngồi"), which is exactly the kind of fact that has no expiry. Dated observations only arrive through the memo card, and in three weeks the room produced two drafts and committed neither. The dated-line rate is currently zero per month, not "low".

## Revisit when

One of these is true, not before:

- A room's `observations.md` passes **~150 lines**, or
- it holds **more than about 20 dated lines**, or
- anyone reports the Notes tab as hard to scan.

At that point the measurement is the same one: count the lines, count the dated ones, count the stale ones. If a sweep is then warranted, the constraint that shapes it is already known — a bulk delete is a **new endpoint**, not a loop over `DELETE .../observations/{id}`, because every delete moves the file's `etag` and N independent deletes would 409 each other. And it posts one summary trail message, not one per line.

The two things that stay true regardless: standing rules must never be touched by any of it (they have no date, so `stale` is structurally `false` — keep it that way rather than inventing an "old rule" notion), and a bot that deletes its own memory on a timer remains a worse failure than a long file.

## Also worth knowing

`observations.count_since(room_id, subject, *, since)` is defined and tested but has no caller in `app/`. It exists so Phoenix can one day say "lần thứ 3 tháng này" from a Python count rather than by eye. Anything that deletes lines silently under-counts it the day it is wired up — one more reason the burden of proof sits with deletion.

## Where the position is written down

- [`../plans/2026-08-14-knowledge-memory-ui.md`](../plans/2026-08-14-knowledge-memory-ui.md) — "Risks & non-goals", the `stale` bullet.
- [`../specs/2026-08-14-lunch-suggestion-memory-design.md`](../specs/2026-08-14-lunch-suggestion-memory-design.md) — D4, two line types with two lifetimes in one file.
- `backend/app/knowledge.py:102-135` — where `stale` is computed, and the docstring that says why nothing prunes.
