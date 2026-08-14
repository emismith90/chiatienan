# Phoenix Knowledge & Memory UI — make what the bot knows visible and editable

> **Status: delivered** — all three phases, one branch. 940 backend tests, 266 frontend tests, and the flows driven end-to-end in a real browser. See [Delivered](#delivered) for what changed against this plan.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phoenix now remembers things — restaurants, standing rules, notes about people, and rolled-up conversation history — and a human can see none of it and edit none of it. Give it the same treatment the ledger got: a panel that presents each kind of knowledge in its own shape (cards, chips, sections — never raw file text) and lets a room member correct it.

**Architecture:** One new right-hand panel, mounted in the *same* shell as `LedgerPanel` (persistent column on desktop, slide-over drawer on phone), reached through a `Ledger | Bộ nhớ` tab strip. Three sub-tabs, one per store, because the three stores have three different lifetimes and three different owners. Reads come from one `GET /api/rooms/{id}/knowledge`. Writes are narrow REST endpoints that take `chat._agent_lock` (the file stores are read-modify-write and the turn loop is the other writer) and are guarded by an `etag` so a stale panel cannot clobber a concurrent agent write. Editors are centred dialogs, reusing the `ProfileDialog` / `MemberInfoDialog` pattern, so the column stays a *list* (widened 260 → 320px for the place cards).

**Tech Stack:** FastAPI + SQLAlchemy (SQLite/WAL), pytest; Next.js 15 App Router + Tailwind v4 + vitest/jsdom.

**Spec:** [`docs/superpowers/specs/2026-08-14-lunch-suggestion-memory-design.md`](../specs/2026-08-14-lunch-suggestion-memory-design.md) — this plan is the frontend that design deferred ("No frontend", phase 2; §7 shipped only the memo card).

---

## What exists today

Three stores, deliberately different shapes (design D4, D6, D7):

| Store | Where | Written by | Human access today |
|---|---|---|---|
| **Places** — restaurant identity | `places` table, room-scoped (`models.py:61`) | `add_place` tool, `seed_places.py` | none |
| **Observations & standing rules** — prose + clock gates | `{DATA_DIR}/rooms/{id}/observations.md`, four pipe-separated fields (`observations.py`) | `remember`/`forget` → memo confirm card | none (the module docstring claims "hand-edited by the operator" — there is no editor, and prod is a droplet) |
| **Conversation memory** — LLM roll-ups | `{DATA_DIR}/rooms/{id}/memory.md` + `memory.meta.json` watermark (`memory.py`) | `/clear`, 10-week rollover | none |

Derived, never stored: `places.stats()` — `times`, `last_on`, `days_since`, `weekday_counts`, `avg_per_head`, `band`.

Two consequences worth naming, because they are the actual bug report:

1. A wrong fact is **unfixable except by talking to the bot about it**. `forget` requires the operator to reproduce the note's text *exactly* (`tools.py`'s `forget` compares `o.text == text`), and nothing shows them what that text is.
2. Memory silently steers `suggest_lunch` — gates can make a place `too_late` and vanish from a suggestion — with no surface where anyone can ask "why did it say that?".

## Decisions

- **K1 — One panel, three tabs, mirroring the three stores.** Not one merged "memory" list. A dated observation decays, a standing rule does not, and a place row is inert until someone eats there; collapsing them into one list would hide exactly the distinction the design spent D4 establishing.
- **K2 — Tab strip inside the side panel, not a fifth header button.** The header cluster already overran 320px once and had to be given `flex-wrap` (`room-view.tsx:507`). `Ledger` / `Bộ nhớ` live as tabs at the top of the panel; the existing mobile `Ledger` button opens the drawer on whichever tab was last used.
- **K3 — Derived numbers are read-only, always.** `times`, `days_since`, `avg_per_head`, `band` and the weekday rhythm are rendered as chips and never as inputs. Editing a computed count is inventing history; D1 puts those numbers in Python precisely so no one can hand-type them.
- **K4 — `slug` is immutable.** It is the `place:` subject in `observations.md`, so renaming a place must keep its slug or every note about it silently detaches. `name` and `aliases` are freely editable; the slug shows in the detail dialog as a muted mono identity line reading "mã định danh, không đổi được". (A true slug rename needs an observations rewrite — out of scope, and it should stay out until someone actually needs it.)
- **K5 — Deleting a place is `active=False`.** `meals.place_id` references it and history must not lose its subject. `closed_until` stays the *temporary* lever (D11, self-expiring); the dialog offers both and labels the difference.
- **K6 — Line-preserving file writes.** `observations.load()` skips comments and malformed lines by design ("one stray line must cost one fact, not lunch"), so any write that rewrites the file *from parsed rows* deletes them. **`observations.remove()` has this bug today** (`observations.py:119`) — it rewrites from `load()` and drops every comment and unparsed line in the file. Fix it as part of this work: edit/delete operate on the raw line list, matching by line index resolved from a content id, and every line the parser did not understand is written back verbatim.
- **K7 — Stable line ids without changing the format.** `id = sha1(f"{when}|{subject}|{gate}|{text}").hexdigest()[:12]`, computed on read. No new field, so the file stays hand-editable (D4) and the seed installer keeps working. Two byte-identical lines collide. **Revised in delivery:** that is *not* fine — a collision makes both lines unaddressable — so an identical add is a no-op and an edit that would collide is refused (see Delivered).
- **K8 — Optimistic concurrency via `etag`.** `GET` returns `sha256` of each file's bytes. Writes send it back; a mismatch is `409` and the panel refetches and says so. (A place `409` is a *name collision*, not this — places carry no etag, so only the etag-guarded callers treat 409 as "reload".) The alternative — last-write-wins on a file the agent also appends to — loses a note whenever someone edits while a turn is running.
- **K9 — Every write takes `chat._agent_lock`.** Exactly what `commit_memo_route` already does (`main.py:641`) and for the same reason. Place writes are DB-only and would be safe without it, but they are taken under it too so the lock discipline is uniform and nobody has to remember which endpoint is which.
- **K10 — Edits are visible to the room.** Memory steers suggestions, so a member quietly deleting "phải gọi trước 11h30" changes everyone's lunch. Each write posts a short `bot`-kind room message ("Nhím đã xoá ghi nhớ về Cơm gà Thịnh Lơ: …"). Cheap (existing `chat.post_message` plumbing), and it gives the file the trail `observations.remove`'s docstring explicitly refuses to keep inside the file itself.
- **K11 — Any room member may edit; no admin gate.** Consistent with the fact that any member can already make Phoenix write a note by asking, and confirm it on the memo card. Auth is `require_session` + `_check_room`, same as the ledger.
- **K12 — The watermark is displayed, never edited.** `summarized_through_id` is the lower bound of the agent's recent-message window; moving it backwards re-summarizes months of chat into duplicate memory sections on the next turn — expensive, LLM-billed, and user-visible. Shown as a footnote ("đã tóm tắt tới 2026-08-01"), read-only.
- **K13 — Gates are pickers, not text.** `busy@12:00` renders as "Đông từ 12:00", `order-by@11:30` as "Đặt trước 11:30", `closes@12:30` as "Đóng cửa 12:30", and the editor is a three-way select plus a time input. This is the whole "not raw text" requirement in miniature: the regex in `_GATE_RE` is the schema, so the UI should be the only place a human ever has to meet it.
- **K14 — Bands in the list, VND in the detail.** The list shows `rẻ/vừa/đắt` (D5's vocabulary, the same words the room hears in a suggestion). The place dialog also shows `avg_per_head` labelled "trung bình/người (tính từ sổ)" — a human is allowed to see money in this app, the ledger shows it everywhere, but it is labelled as derived from the ledger so it is never mistaken for a figure someone owes.

## API surface

```
GET    /api/rooms/{room_id}/knowledge            → { etags, places[], observations[], memory{} }
POST   /api/rooms/{room_id}/places               → create (slug from name, 409 on collision)
PATCH  /api/rooms/{room_id}/places/{place_id}    → name/aliases/tags/delivery/address/phone/
                                                    walkable/walk_minutes/price_hint/closed_until/active
DELETE /api/rooms/{room_id}/places/{place_id}    → soft (active=False)   [K5]
POST   /api/rooms/{room_id}/observations         → { subject, when|standing, gate?, text }
PATCH  /api/rooms/{room_id}/observations/{id}    → { etag, when?, gate?, text? }              [K7,K8]
DELETE /api/rooms/{room_id}/observations/{id}    → ?etag=…                                    [K7,K8]
PATCH  /api/rooms/{room_id}/memory/sections/{i}  → { etag, text }
DELETE /api/rooms/{room_id}/memory/sections/{i}  → ?etag=…
```

`GET` response, per store:

- `places[]`: every editable column + `stats` (`times`, `days_since`, `band`, `avg_per_head`, `weekday_counts`, `last_on`) + `note_count`. Includes inactive rows, flagged, so a soft-deleted place can be brought back.
- `observations[]`: `{ id, when: "YYYY-MM-DD" | null, subject, subject_label, subject_kind, gate, gate_label, text, stale }`. `subject_label` is resolved server-side (place name / member display name) — the panel must never have to reconstruct "who is `member:le-hoang-hung`". `stale` is `when < today - DEFAULT_SINCE_DAYS`, i.e. the line is in the file but `for_subjects` no longer feeds it to the model.
- `memory`: `{ sections: [{ index, header, date, body }], watermark: { through_id, through_at }, etag }`, parsed from the `## {header} — {date}` structure `append_summary` writes.

All writes publish `{"type": "knowledge:changed"}` on the room hub, and `commit_memo_route` gains the same publish (it writes observations today and the panel would not notice).

## File Structure

Backend:
- `backend/app/knowledge.py` — **create**: read model (assemble the GET payload, resolve subject labels, compute ids/etags/staleness) + line-preserving file editors.
- `backend/app/observations.py` — **modify**: `line_id`, `raw_lines()`, `replace_line`/`delete_line` that preserve unparsed lines; fix `remove()` (K6).
- `backend/app/memory.py` — **modify**: `parse_sections`, `write_sections`, `file_etag`.
- `backend/app/places.py` — **modify**: `update_place`, `create_place` (shared with the `add_place` tool so the slug rule lives in one place).
- `backend/app/main.py` — **modify**: the nine routes above + Pydantic bodies.
- `backend/tests/` — **create**: `test_knowledge_api.py`, `test_observations_edit.py`, `test_memory_sections.py`.

Frontend:
- `frontend/src/hooks/use-knowledge.ts` — **create**: mirrors `use-ledger.ts`, keyed on a `knowledgeVersion` from `use-room`.
- `frontend/src/hooks/use-room.ts` — **modify**: `knowledge:changed` → `knowledgeVersion + 1` (one line, beside `ledger:changed` at `:42`).
- `frontend/src/components/chat/side-panel.tsx` — **create**: `Ledger | Bộ nhớ` tab strip; hosts `LedgerPanel` / `KnowledgePanel`.
- `frontend/src/components/chat/knowledge-panel.tsx` — **create**: `Quán | Ghi nhớ | Nhật ký` sub-tabs + search.
- `frontend/src/components/chat/place-card.tsx`, `place-dialog.tsx` — **create**.
- `frontend/src/components/chat/observation-row.tsx`, `observation-dialog.tsx` — **create**: gate chips + pickers (K13).
- `frontend/src/components/chat/memory-sections.tsx` — **create**: collapsible dated sections, per-section edit.
- `frontend/src/components/chat/room-view.tsx` — **modify**: mount `SidePanel` in both the desktop column and the drawer instead of `LedgerPanel` twice; widen the desktop column to `lg:w-[320px]`.
- `frontend/src/lib/api.ts` — **modify**: typed calls + types.
- `frontend/src/components/chat/__tests__/` — **create**: panel, place dialog, observation dialog, memory sections.

---

## Phase 1 — Visible (read-only)

Ships the whole "it is not visible" half on its own, with no write path to get wrong.

### Task 1: `knowledge.py` read model

**Files:** create `backend/app/knowledge.py`; modify `backend/app/observations.py` (`line_id`), `backend/app/memory.py` (`parse_sections`, `file_etag`); test `backend/tests/test_knowledge_api.py`.

**Interfaces:** `knowledge.snapshot(session, room_id, *, today=None) -> dict` — the full GET payload.

- [x] Failing test: a room with two places (one `chưa-thử`, one with three linked meals), four observation lines (one `always` + gate, one recent, one 200 days old, one deliberately malformed) and a two-section `memory.md` produces: both places with correct `times`/`band`, three parsed observations with stable ids and the old one `stale: true`, the malformed line absent from the payload *and still present in the file*, and two memory sections with their dates.
- [x] Implement. Subject labels resolve through `places.list_places` / `roster.list_members(include_inactive=True)` — a note about a since-removed member must still render their name, not `member:nhim`.
- [x] An unresolvable subject (place deleted, member gone from the DB) renders with `subject_label = subject` and `subject_kind = "unknown"` rather than being dropped. A note you cannot see is a note you cannot delete.
- [x] Commit.

### Task 2: `GET /api/rooms/{id}/knowledge`

**Files:** modify `backend/app/main.py`; test `backend/tests/test_knowledge_api.py`.

- [x] Failing test: 200 for a member of the room, 403 for a session belonging to another room (`_check_room`), and the payload shape above.
- [x] Implement, `require_session` + `_check_room` (K11). Read-only, so no lock.
- [x] Commit.

### Task 3: The panel shell

**Files:** create `side-panel.tsx`, `knowledge-panel.tsx`, `use-knowledge.ts`; modify `room-view.tsx`, `api.ts`; test `frontend/src/components/chat/__tests__/side-panel.test.tsx`.

- [x] Failing test: the drawer renders a `Ledger` / `Bộ nhớ` tab strip; switching to `Bộ nhớ` renders the three sub-tabs; the ledger tab still renders `StatementSections`; the last-used tab survives closing and reopening the drawer.
- [x] Implement. Both mount sites (desktop column, phone drawer) take `SidePanel`; tab state lifts to `RoomView` so the two share it. `aria-selected` on the tabs, arrow-key movement, and the drawer keeps its existing Esc handling.
- [x] Commit.

### Task 4: Rendering the three stores

**Files:** create `place-card.tsx`, `observation-row.tsx`, `memory-sections.tsx`; tests alongside.

- [x] Failing tests, one per store: a place card shows name, band chip, `times`/`days_since` in Vietnamese ("4 lần · 12 ngày trước"), tag chips, a `chưa-thử` badge, a `Đóng tới 20/8` badge when `closed_until` is set and a muted style when `active: false`; an observation row shows a gate chip in human words (K13) and a `Cũ` marker when `stale`; memory renders as collapsed dated sections plus the read-only watermark footnote (K12).
- [x] Implement. **Quán** is a search-filtered list (100 seeded places — a search box is not optional), ordered by `times` desc then name. **Ghi nhớ** groups by subject and splits `Quy tắc` (always) from `Ghi nhớ` (dated, newest first). **Nhật ký** collapses every section but the newest.
- [x] Empty states that say how the store gets filled ("Chưa có ghi nhớ nào — nói với @phoenix «quán X phải gọi trước 11h30»"), not a bare "no data".
- [x] Commit.

### Task 5: Live refresh

**Files:** modify `backend/app/main.py` (`commit_memo_route`), `frontend/src/hooks/use-room.ts`; test both sides.

- [x] Failing test: committing a memo publishes `knowledge:changed`; the hook bumps `knowledgeVersion` and `use-knowledge` refetches.
- [x] Implement. Commit.

## Phase 2 — Editable: places and observations

The two stores that steer `suggest_lunch`, so they are the two worth fixing by hand.

### Task 6: Line-preserving observation writes (K6)

**Files:** modify `backend/app/observations.py`; test `backend/tests/test_observations_edit.py`.

**Interfaces:** `observations.replace_line(room_id, line_id, obs) -> bool`, `observations.delete_line(room_id, line_id) -> bool`, `observations.file_etag(room_id) -> str`.

- [x] Failing test: a file containing a comment, a malformed line and three good lines survives an edit and a delete with the comment and the malformed line **byte-identical and in position**; the existing `remove()` gets the same guarantee (it does not today); a `line_id` that no longer exists returns `False` rather than raising.
- [x] Implement over the raw line list, not over `load()`.
- [x] Commit.

### Task 7: Observation write endpoints

**Files:** modify `backend/app/main.py`; test `backend/tests/test_knowledge_api.py`.

- [x] Failing test: POST appends a well-formed line; PATCH with a stale `etag` returns `409` and leaves the file untouched; DELETE with a current etag removes exactly one line; each write happens under `chat._agent_lock` and publishes `knowledge:changed`; a `gate` that fails `_GATE_RE` is a `422`, not a silently-dropped field.
- [x] Implement (K8, K9). `subject` arrives as an explicit `place:<slug>` / `member:<nickname>` from the panel — the UI already knows which subject the user tapped, so `_memo_subject`'s free-text guessing (and its D18 ambiguity risk) is not in this path at all.
- [x] Commit.

### Task 8: Place write endpoints

**Files:** modify `backend/app/places.py`, `backend/app/main.py`; test `backend/tests/test_knowledge_api.py`.

- [x] Failing test: PATCH `name` leaves `slug` untouched and keeps the place's observations attached (K4); POST with a name that slugifies onto an existing place returns `409` naming the existing place; DELETE sets `active=False` and leaves `meals.place_id` intact (K5); `walk_minutes`/`price_hint` accept `null` to clear.
- [x] Implement; `add_place` (tool) and `POST /places` share one `places.create_place`.
- [x] Commit.

### Task 9: The editor dialogs

**Files:** create `place-dialog.tsx`, `observation-dialog.tsx`; modify `knowledge-panel.tsx`, `api.ts`; tests alongside.

- [x] Failing tests: the place dialog round-trips every editable field, renders slug read-only with an explanation, shows derived stats as text (K3), and offers "Đóng tạm tới…" and "Ẩn quán" as distinct actions; the observation dialog has a `Quy tắc (always)` ⇄ `Ngày cụ thể` toggle that swaps a date input in and out, a three-way gate select + time input, and a delete with confirmation; a `409` shows "Có người vừa sửa — đã tải lại" and refetches rather than retrying blind.
- [x] Implement as centred dialogs on the `ProfileDialog` pattern (focus trap, Esc, backdrop click), so the 260/320px column never has to host a form.
- [x] Add "＋ Thêm quán" / "＋ Thêm ghi nhớ" on the respective sub-tabs.
- [x] Commit.

### Task 10: The room-visible trail (K10)

**Files:** modify `backend/app/main.py`; test `backend/tests/test_knowledge_api.py`.

- [x] Failing test: each write posts one `bot`-kind message naming the actor, the subject and the change, and publishes it — and a place `PATCH` that changes nothing posts nothing.
- [x] Implement. One short Vietnamese line per change; never echo a money figure (money-safety, D3).
- [x] Commit.

## Phase 3 — Editable: conversation memory

Last, because it is the store with the least leverage per edit and the most rope.

### Task 11: Memory section writes

**Files:** modify `backend/app/memory.py`, `backend/app/main.py`; test `backend/tests/test_memory_sections.py`.

- [x] Failing test: `parse_sections` → `write_sections` on an untouched file is byte-identical (round-trip safety before anything is allowed to edit it); editing section 0 leaves section 1 and any preamble text alone; deleting the last section leaves the watermark untouched (K12); stale `etag` → `409`; writes take the lock.
- [x] Implement. Reject a body containing `\n## ` — one section cannot smuggle in another.
- [x] Commit.

### Task 12: Memory editing UI

**Files:** modify `memory-sections.tsx`, `api.ts`; test alongside.

- [x] Failing test: expanding a section reveals `Sửa` / `Xoá`; edit swaps in a textarea with save/cancel; delete confirms first; the watermark line has no control.
- [x] Implement. Commit.

### Task 13: Notes where the person already is

**Files:** modify `frontend/src/components/chat/room-view.tsx` (`MemberInfoDialog`, `ProfileDialog`); test alongside.

- [x] Failing test: a member dialog lists that member's `member:` observations, with the same rows and the same edit affordance as the panel.
- [x] Implement — the same components, reused; no second renderer for the same data. Commit.

---

## Delivered

Shipped as planned, with these deviations — all of them things the plan could not have known:

- **`observations.md` did have the data-loss bug K6 predicted**, and so did `remove()`. Fixed: `indexed()` reports raw line numbers, and `replace_line`/`delete_line`/`remove` edit that list in place. A file with a comment and an unparsable line survives an edit byte-for-byte (`test_observations_edit.py`).
- **`_GATE_RE` accepted hours 20–29.** `[0-2]\d` matched `25`, so a hand-typed `busy@25:00` passed validation and then reached `now.replace(hour=25)` in `gate_status` — one typo raising `ValueError` inside every `suggest_lunch` for that room. Bounded at 23, where a bad gate degrades to prose like anything else the parser cannot read. Found by writing the `parse_gate` test, not by reading the regex.
- **Two duplicate guards the plan missed.** K7 waved off `line_id` collisions as harmless ("the same fact twice"), but a collision means neither line can be addressed again *and* React sees duplicate keys. So: an identical `POST` is a no-op returning `already_existed` (the `add_place` shape), an edit that would collide is a `422`, and the list keys are index-suffixed so a hand-edited file already holding a pair still renders. Caught by driving the real UI twice.
- **`writeError` originally mapped every 409 to "someone else edited this".** Wrong for places, whose 409 means "that name is taken" — places carry no etag. The server's own detail is now shown, and only the etag-guarded callers reload.
- **The dialog needed a pinned action row.** A place has eleven fields; on a 900px viewport the form overflowed `max-h-[85dvh]` and Save was off screen. `PanelDialog` takes a `footer` that sits below the scroller.
- **`subject_id` added to the read model.** Without it, "notes about this person" in the member dialog would have meant "notes whose label matches this display name", which is wrong the day two members share one.
- **Both `member:` spellings resolve to one person.** The seed installer writes the nickname and `tools._memo_subject` writes the folded display name; both are already in the wild. `SubjectIndex` indexes every form and reports a canonical `subject_key`, so the panel groups them under one heading without rewriting anyone's file.
- **Names differ from the plan:** `note-row.tsx` / `note-dialog.tsx` (not `observation-*`), plus a `knowledge-ui.tsx` holding the dialog shell, chips and label helpers the three views share.
- **The weekday rhythm is a sentence, not a chart.** "hay ăn T5" beats a seven-bar micro-chart of `[0,1,0,2,0,1,0]` at 320px, and it would have been the only chart in the app.

Verified in the browser (not only in jsdom): adding a standing rule through the gate picker and seeing it render as "Đặt trước 10:45"; editing it into a dated note with the gate cleared; renaming Quán Bé Bự and watching its notes follow the rename (K4 proven live, not just unit-tested); and a `📓` trail message landing in the room for each write.

## Risks & non-goals

- **Slug renames are not supported** (K4). If a room genuinely needs one, it is a migration that rewrites `observations.md` subjects under the lock, and it deserves its own plan. **It now has one:** [`2026-08-14-place-slug-rename.md`](2026-08-14-place-slug-rename.md).
- **`stale` is display-only.** A stale observation still sits in the file and is still fed to the model the moment `DEFAULT_SINCE_DAYS` moves. Auto-pruning old notes is deliberately not in scope: the file is small, and a bot that deletes its own memory on a timer is a worse failure than a long file. **Measured 2026-08-14 and still the right answer:** production's only real room holds 42 observation lines, all of them standing rules, so nothing there can go stale at all — [`../notes/2026-08-14-stale-observations-measured.md`](../notes/2026-08-14-stale-observations-measured.md).
- **No conflict *merge*.** `409` means "refetch and redo"; a three-way merge of a prose note is not worth the code.
- **Payload size.** 100 places × ~14 fields + stats is ~20–30KB per fetch. Fine at the current scale, and it refetches only on `knowledge:changed`. If a room's list ever grows past a few hundred, the GET takes a `?section=` narrowing (already in the design) and the sub-tabs fetch lazily.
- **`price_hint` becomes hand-editable**, which slightly softens D8's "seed-time fallback" framing. It stays labelled as a hint and is still ignored the moment real meals link, so a wrong hint decays on its own.
