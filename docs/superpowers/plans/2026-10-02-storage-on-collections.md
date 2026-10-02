# Storage on collections — notes, places and the ledger move into `kn_documents`

Written 2026-10-02 on `feat/storage-on-collections`, after PR #58 (journal collections +
hybrid search).

**The ask.** Move every room-scoped store onto the generic collection engine
(`kernos.data`): the notes file (`observations.md`), the `places` table, and the money
ledger (meals, payments, settlements, poker games) onto **journals**. Embed existing data
for search. Dry-run the migration on a clone of production first; only if every number is
identical, ship it — with a backup so production can be rolled back.

**What this is, honestly.** The ledger already behaves like an event log: nothing ever
deletes a money row, and the only post-insert writes are void flags, a payment's
`meal_id` being re-pointed, and the place-link backfill (§1.4). So the ledger part is
*event sourcing*: the journal becomes the system of record, and balances stay what they
are today — derived by `ledger_core` code, never stored. **No money rule moves into
data.** Invariants stay in code (§1.6). What changes is where rows live and how they are
read.

---

## 1. Facts from the code

### 1.1 Notes (`app/observations.py`)
1. One file per room, `{DATA_DIR}/rooms/{id}/observations.md`, one fact per line:
   `- when | subject | gate | text`. `when` is a date or `always` (a standing rule).
2. `line_id` = `sha1(to_line())[:12]` (observations.py:72-85): **content-derived**, so an
   edit changes it and two identical lines are one fact. The UI addresses PATCH/DELETE
   by it and refetches after every write (it never reads write responses' ids).
3. `file_etag` = `sha256(file bytes)[:16]`; PATCH and DELETE refuse a stale etag (409,
   main.py:1069, 1114). POST takes none.
4. Callers: main.py (POST/PATCH/DELETE `/observations`), memos.py (commit/remove),
   packs/places.py (`for_subjects`, `gate_status`, `load`), knowledge.py (panel rows,
   etag), seed_places.py (install), places.py (`retarget_subject` on slug rename).
   `indexed` and `count_since` have no production caller.
5. The debug export API cannot read these files (DB tables only), so the dry run cannot
   use production's real notes (§3, D11).

### 1.2 Places (`app/places.py`, `app/models.py:67`)
1. Production: **101 rows** in room 3. Columns: id, room_id, slug (unique per room),
   former_slugs, name, aliases, tags, delivery, address, walkable, walk_minutes, phone,
   price_hint, closed_until, active, created_at.
2. Mutations outside places.py: PATCH route (`apply_edits` then flush, main.py:926),
   DELETE route (sets `active=False`, main.py:950), seed CLI (`setattr` per field,
   seed_places.py:109). Places are hidden, never deleted.
3. `rename_slug` moves the slug in three stores (row, notes subjects, pending memo cards)
   and appends the old slug to `former_slugs`.
4. **`meals.place_id` is an integer with no foreign key** (ledger_core/models.py:44).
   Production: 10 of 47 meals carry one. The frontend never reads it.
5. `stats` (places.py:361) joins meals/meal_shares by place_id; `backfill_links` UPDATEs
   `meals.place_id`.
6. No boot-time seeding: `LunchPlacesPack.seed()` is a no-op; the seed is a manual CLI.

### 1.3 Ledger tables
| table | prod rows | updated after insert by |
|---|---|---|
| `meals` (+`meal_shares` 211) | 47 | `void_meal` (voided/voided_by/voided_at); `backfill_links` (place_id) |
| `payments` | 135 | `void_payment`; `repoint_meal_payments` (meal_id→NULL); `_repoint_or_void_edited_payment` (meal_id→new id; or voided **without** voided_by/at) |
| `settlements` | 0 | never (`record_settlement` has **no production caller**) |
| `games` / `game_entries` (poker) | 0 / 0 | `void_game` |

No code path DELETEs a ledger row (debug_api.py:381-403 edits a snapshot copy only).

### 1.4 How the ledger is read
`meal_edges` / `debt_breakdown` / `period_transfers` / `statement_for` /
`outstanding_pairs` / timelines (ledger.py) select rows, then do everything else in Python:
`build_debt_edges` → `apply_payments_fifo` → `_net_pairs` (money.py). Poker contributes
edges through the registry (`app/kernel.py:88`). FIFO runs over the **whole** ledger;
windows filter edges, not payments (ledger.py:404).

### 1.5 Integer ids are load-bearing
`QuickPayIn.meal_id: int`, route params `payment_id: int`, `isinstance(meal_id, int)` in
the void tools, tool schemas `{"type": "integer"}`, moneyguard's `#\s*(\d+)` → `int(n)`,
`session.get(Meal, id)` in drafts/validate, the FIFO pool sort key
`(occurred_on, ref_kind, meal_id)`, "Recorded #N" bodies, frontend `number` types. Ids are
global autoincrement today (unique across rooms).

### 1.6 Invariants a storage change must keep (each enforced in code today)
1. Meals are immutable; a correction is void + re-record. Σshares = total. Balances are
   derived, never stored.
2. A void keeps the record as evidence; payments survive a meal void (untarget vs
   repoint vs visible void, drafts.py:184-222).
3. **A draft commit's ledger rows, its bot card and the draft's status flip land in one
   transaction** (drafts.py:148, one `db.session()`), under `_agent_lock`.
4. No ledger write without a confirmed card; the model never writes.
5. An edit is refused when the meal is on or before the last settlement's `period_to`.
6. moneyguard: a claimed "#N" must be a live, room-scoped, non-voided record; amounts are
   backed by tool output. Fails closed.
7. Determinism: FIFO order, member-id netting order, `last_settlement` tiebreak.
8. The debug export must still cover the ledger (test_ledger_core_extraction.py:22).

### 1.7 The data plane as it stands (PR #58)
1. Collections belong to a **business**; documents to `(collection_id, space_id)`.
   **Re-binding a room to another business hides its documents** (by design, models.py
   Document docstring).
2. Every `DataStore` method opens its **own** session — no way to join a caller's
   transaction.
3. Any collection a business defines becomes agent tools for every profile that enables
   the `collections` pack (lunch does not today; tests do).
4. `MAX_DOCUMENTS` 1000 per (space, table); `MAX_JOURNAL_ENTRIES` 10000 per (space,
   journal). Journal doc ids are `000001…` per space.

---

## 2. Decisions

**D1 — A `system` owner for internal collections.** Notes, places and the ledger live in
collections owned by a reserved business `_system`, not by the room's business, so
re-binding a room (§1.7.1) can never hide its money, places or notes. `_system` is
seeded at boot and is not bindable.

**D2 — Internal collections generate no agent tools.** A collection gains
`internal: bool`. `CollectionsPack` skips internal ones, and the admin API refuses to
create, change or delete them (they are defined in code). Otherwise enabling the
`collections` pack would hand the model `ledger_append` — a write with no confirm card,
breaking invariant 1.6.4.

**D3 — `DataStore` writes and reads can join the caller's transaction.** Every document
method takes an optional `session`; when given, it neither opens nor commits one. This is
what keeps invariant 1.6.3: a draft commit appends its ledger entry, posts its card and
flips the draft in one transaction.

**D4 — One ledger journal per room, event-shaped entries.** A single `ledger` journal,
`data.event` one of:

| event | data | replaces |
|---|---|---|
| `meal` | meal_id, occurred_on, payer, total, dish, note, raw_input, initiator, guests, source, logged_by, place_id, created_at, shares[{member_id, amount}] | meals + meal_shares insert |
| `meal_void` | meal_id, by, at | `void_meal` UPDATE |
| `payment` | payment_id, from, to, amount, occurred_on, meal_id, ref_kind, note, source, logged_by, created_at | payments insert |
| `payment_void` | payment_id, by, at | `void_payment` / the edit path's void |
| `payment_retarget` | payment_id, meal_id (int or null) | `repoint_meal_payments`, `_repoint_or_void_edited_payment` |
| `meal_place` | meal_id, place_id | `backfill_links` UPDATE |
| `settlement` | settlement_id, period_from, period_to, requested_by, transfers, created_at | settlements insert |
| `game` / `game_void` | game_id, played_on, house, note, …, entries[{member_id, buy_in, cash_out}] / game_id, by, at | poker tables |

One journal, not one per table, because the state is a fold over *all* events in order (a
void must follow its meal; a retarget names both a payment and a meal). The collection
schema stays loose (`event` enum + optional fields); **`ledger_core` validates each event
in code** before appending.

**D5 — Business ids stay integers, separate from journal doc ids.** `meal_id`,
`payment_id`, `game_id`, `settlement_id` live inside the entry and keep today's values.
New ids come from a `_system` sequence (`sequences` table collection, one doc per name,
incremented in the same transaction as the append), seeded above the current
`MAX(id)` of each old table. Global uniqueness is kept. Nothing in §1.5 changes.

**D6 — A projection, not a rewrite of the money code.** `ledger_core` gains a
`LedgerView` that folds a room's journal into the record objects the code already uses
(`Meal`-like with `.shares`, `Payment`-like, `Settlement`-like, `Game`-like — same
attribute names, including `voided`, `voided_by`, `voided_at`, `place_id`). Every read
in §1.4 swaps `select(Meal)…` for `view.meals()` / `view.payments()`;
`build_debt_edges`, `apply_payments_fifo` and `_net_pairs` are untouched. The fold is
in-memory per call: production has ~190 entries per room; it is a few ms.

**D7 — Writes become appends.** `record_meal` → `meal` entry; `void_meal` → `meal_void`;
`record_payment` → `payment`; `void_payment` → `payment_void`; repoint → `payment_retarget`;
`backfill_links` → `meal_place`; poker likewise. Each keeps its current signature and
validation, so callers do not change. `MAX_JOURNAL_ENTRIES` is raised for the ledger
(per-collection cap: 100,000).

**D8 — Places: a `places` table collection, `doc_id` = the integer id as text.**
Migrated rows keep their id (`"57"`), so `meals.place_id` / `meal.place_id` stay valid and
no meal is rewritten. New ids come from the same sequence mechanism (D5). Slug uniqueness
per room (was a DB constraint) is enforced in `places.py`. `places.py` returns
`PlaceRecord` dataclasses with the model's attribute names; the three mutation sites
(§1.2.2) call `places.save(...)` instead of mutating a row. Hidden places stay
(`active=false`).

**D9 — Notes: a `notes` table collection, `doc_id` = the content `line_id`.** Keeps the
UI contract (§1.1.2): ids are content-derived, identical facts collide, an edit is
delete+insert in one transaction. `etag` = hash of the room's sorted doc ids (any write
changes it). The `Observation` dataclass and module functions keep their signatures.

**D10 — Search over the new stores.** `places` searchable on name, aliases, tags, address;
`notes` on text. The exact resolver (`resolve_one`, `CONFIDENT_TIERS`) is **not** replaced
by search — it decides meal links (money history). `find_places` gains hybrid search for
discovery only, as a new optional `query` argument; embeddings backfill lazily on the first
search (§PR #58), ~$0.0005 for production's 101 places.

**D11 — Migration at boot, idempotent, verified, non-destructive.**
1. Runs in `Kernel.__init__` after seeding, under a `_system` marker document
   (`migrations/<name>`) so it runs once.
2. Copies, never moves: old tables and `observations.md` are **read only**; nothing is
   dropped, updated or renamed. A marker file `observations.migrated` records the import.
3. **Self-check before switching:** for every room, the old code on the old tables and the
   new code on the journal must produce identical `debt_breakdown`, `period_transfers`,
   `outstanding_pairs`, `statement_for` (every member), `meal_timeline` and place `stats`.
   Any difference → the migration transaction rolls back, the app logs it and **stays on
   the old storage** (a `storage_mode` flag read at startup), so a bad migration degrades
   to "nothing changed" rather than wrong balances.
4. The old read code stays in the tree for one release, used only by the self-check and
   the dry run (`ledger_core/legacy.py`), then is deleted.

**D12 — Backup and rollback.**
1. `deploy.yml` gains a step before `docker compose pull`: a WAL-safe
   `sqlite3.connect(db).backup(...)` to `/data/backups/pre-<sha>-<ts>.db`, plus a tar of
   `/data/rooms/*/observations.md`. The step fails the deploy if the backup fails.
2. Rollback = restore that file (`deploy/DEBUGGING.md` §3 procedure) and redeploy the
   previous SHA. Because old tables are never written after the switch, **any money
   written after the migration exists only in the journal**: a rollback loses it unless it
   is replayed. `ledger_core/legacy.py` gets a `replay_to_tables` script for that case.

**D13 — Dry run on a production clone is the ship gate.** `python -m
app.migrate_storage --db <clone> --check` runs D11 on a copy of the production snapshot
(`/internal/debug/db`, sanitised: pins and account numbers redacted, every amount kept) and
prints per-room diffs. Ship only on "0 differences". Notes are dry-run on
`seeds/observations-local.md` (§1.1.5); their migration is lossless by construction (a
line becomes a document with the same four fields) and the file is kept.

**D14 — Out of scope.** No runtime rule builder (the ledger's rules stay code; the open
question from 2026-10-02 — who writes rules, which rules — is unanswered). No change to
how balances are computed. No new UI beyond what D10 needs.

---

## 3. Tasks

Each task lists its proof. Work in this order; every task ends green.

### S1 — data plane prerequisites
- `_system` business seeded and refused by bindings; `internal` collections: no tools, admin
  API refuses writes; `session=` on every `DataStore` document method; per-collection cap;
  `sequences` helper (`next_id(name, *, session, floor)`).
- **Proof:** tests — an internal collection yields no tools with the pack enabled; admin PUT
  on it is 422; a write with `session=` rolls back with the caller's exception; `next_id`
  is monotonic and respects `floor`; binding `_system` is refused.

### S2 — notes on `notes`
- observations.py rewritten over the collection (signatures kept); etag per D9; seed
  installer and memos unchanged at their call sites.
- **Proof:** test_observations*, test_memos, test_knowledge_api pass with fixtures moved
  from file writes to a `make_note` helper; a new test shows identical-content POST reports
  `already_existed`, and a stale etag still 409s.

### S3 — places on `places`
- places.py over the collection, `PlaceRecord`, `save`, slug uniqueness, `next_id`; routes
  and seed CLI call `save`; `stats` reads meals through the `LedgerView` (after S4) or the
  legacy reader (before S4).
- **Proof:** test_place*, test_places*, test_suggest_lunch, test_meal_place_link pass with a
  `make_place` helper; rename still moves notes and pending memos; a new place gets
  `max(old id)+1`.

### S4 — the ledger journal
- Event validation, `LedgerView`, appends for every write path in §1.3, poker included.
  `record_settlement` too (no prod caller, but tests use it).
- **Proof:** the whole suite, including test_golden_meals, test_scenario_week, poker golden,
  test_run_bot_turn_golden byte-identical; a new property test generates random sequences
  of meals/payments/voids/retargets and asserts old-tables vs journal produce identical
  `debt_breakdown` and statements.

### S5 — migration + self-check + flag
- `app/migrate_storage.py` (boot hook and CLI), legacy reader, marker docs, `storage_mode`.
- **Proof:** migrating a copy of `test_prod_migration`'s production-shaped DB is a no-op the
  second time; a deliberately corrupted journal (one share off by 1đ) makes the self-check
  refuse and the app stay on old storage.

### S6 — dry run on the production clone
- Run S5's CLI on `prod-2026-10-02.db` (scratchpad, never committed).
- **Proof:** "0 differences" across all 4 rooms, pasted into this plan's §6.

### S7 — backup step, search, docs
- deploy.yml backup step; `find_places` `query`; architecture/developer docs; PRIVACY (no
  change: same provider).
- **Proof:** workflow YAML lint; a test for `find_places(query=…)`; one benchmark run
  (typical ×3) because the tool manifest changes.

---

## 4. Open questions for the review gate
1. Is one `ledger` journal (D4) right, or does a per-event-type split make the fold or the
   caps worse/better?
2. D11.3 compares old and new outputs at boot. Is "stay on old storage on mismatch" safe if
   the mismatch is in the *old* code (a latent bug the new fold fixes)?
3. D5 keeps global integer ids via a sequence document. Any path that inserts concurrently
   outside `_agent_lock` (quick_pay, void routes take it; seed CLI does not) that could race
   the sequence?
4. D12.2: is replaying journal-only writes to tables on rollback realistic, or should the
   rollback window simply be "before anyone records money after the deploy"?
5. Anything in §1.6 this plan does not preserve.
