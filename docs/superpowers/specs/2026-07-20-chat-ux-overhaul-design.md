# Chat UX Overhaul — Design

**Date:** 2026-07-20
**Status:** Approved (brainstorming) — ready for implementation plan
**Scope:** The room-chat experience of the chiatienan PWA lunch-splitting bot.

---

## 1. Motivation

A review of the current chat path surfaced five gaps (user-reported), all tracing
back to one architectural fact: the backend runs the agent **to completion**
(`run_turn` in `backend/app/agent.py`) and posts **one** finished bot message.
The SSE stream (`backend/app/main.py`, `backend/app/realtime.py`) carries only
three event types — `message`, `bot.typing`, `bot.done`.

| # | Gap | Root cause |
|---|-----|-----------|
| 1 | No meaningful loading feedback | Only a binary "typing…" indicator; no optimistic echo of the user's own message. |
| 2 | Agent activity not streamed | Turn runs to completion; the rich AG-UI event stream (present in the Atlas reference) is not wired here. |
| 3 | No `@` autocomplete | Composer is a plain `<textarea>`; `ToolContext.turn_mentions` plumbing exists but is never populated. |
| 4 | Expense logging is unguided | LLM parses free text and writes straight to the ledger; no confirm, no member picker, no occasional (non-member) participant. |
| 5 | No "who owes who" after logging | The meal card shows only the split just recorded. |

Plus two additions from the design conversation:

- **Auto-save on supersede** — a proposed draft should save itself when a newer
  meal is proposed (no explicit confirmation required).
- **Story metadata** — capture the original free text plus optional non-money
  fields (món ăn, ai rủ, and a free note for cases like "An đổi ý").

## 2. Guiding principle (unchanged, non-negotiable)

**Money-safety (design D3) is absolute:** every authoritative number is computed
server-side and rendered from a structured payload — never from LLM prose. This
overhaul *strengthens* it: the LLM's job becomes to **propose**, never to
**write**. All ledger writes go through deterministic server code.

## 3. Decisions (from brainstorming)

| Topic | Decision |
|-------|----------|
| Expense flow | Agent-driven draft card (HITL), softened to **optimistic auto-commit** |
| Occasional guest money model | **Guest pays their share in cash** (guests shrink per-head, are never billed) |
| `@` autocomplete scope | **`@bot` only** |
| Streaming depth | **Full tool timeline** (every AG-UI step rendered, auto-collapsing after finish) |
| Balance-after-log view | **Full per-person paid/consumed/balance table** |
| Auto-save trigger | **Only when superseded** by a newer draft (no timer) |
| Metadata shape | **Few labeled fields (dish, initiator) + free note**, raw message always stored |
| `record_meal` agent tool | **Removed** from the agent's toolset — agent only proposes; writes are deterministic |
| Settlement (`settle_period`) | **Unchanged** — out of scope for this overhaul |

---

## 4. Design

### 4.1 Live agent timeline (gaps 1 + 2)

Port the reference translator (`Atlas/.../agent/cursor_agui.py`) into a lean
backend adapter (`backend/app/agui.py`). As `run_turn` iterates
`run.messages()`, it publishes per-turn events to the existing `RoomHub` in
addition to accumulating the `TurnResult`:

- `agent.run.started {turn_id}`
- `agent.text.delta {turn_id, delta}`
- `agent.tool.start {turn_id, call_id, name, args}`
- `agent.tool.result {turn_id, call_id, result}`
- `agent.run.finished {turn_id}`
- `agent.run.error {turn_id, message}`

`turn_id` is a server-generated id (uuid hex) included on every event and on the
final bot message, so the frontend can group a turn's timeline and attach it to
the eventual message.

**Key invariant:** `run_turn` still returns the same `TurnResult`, and the
**final** bot message is posted exactly as today — server-rendered body +
structured attachments. The stream is **live-only progress**: not persisted. A
client that reloads or connects mid-turn simply receives the final message via
catch-up (`GET /messages`), with no timeline. This is acceptable and keeps the
append-only message model clean.

**Frontend.** `mergeEvent` (`frontend/src/hooks/use-room.ts`) gains a
`timelines: Record<turn_id, TimelineStep[]>` slice. A new `<AgentTimeline>`
component renders every step (tool name, args, result) — the "full timeline"
choice. On `agent.run.finished` (and once the bot message lands), the timeline
**auto-collapses** into a one-line summary ("▸ N bước") that expands on tap, so
scrollback stays readable. This replaces the binary typing dots in
`room-view.tsx`.

### 4.2 Composer: `@bot` dropdown + optimistic echo (gap 3)

- **`@` autocomplete.** Typing `@` opens a caret-anchored dropdown listing the
  bot handle(s) from `settings.bot_handle` (exposed to the frontend via a small
  addition to the room-info or members payload). Keyboard: ↑/↓ to move, Enter/Tab
  to accept, Esc to dismiss; mouse click also accepts. Accepting inserts
  `@bot `. Scope is bot-only.
- **Optimistic echo (bonus fix).** On send, immediately append a greyed
  "pending" bubble with a temp id. Reconcile when the real `message` SSE event
  arrives — dedupe by (author_id, body) and swap temp id → real id. Fixes the
  lag where the user's own message appears only after POST → SSE round-trips. On
  send failure, mark the pending bubble as errored with a retry affordance.

### 4.3 Expense flow — optimistic HITL (gap 4 + auto-save)

The draft is a **live server-side record**; the card is its editor.

**Tool change.** New agent tool **`propose_meal`** replaces `record_meal` in the
agent's toolset. It computes a preview and **writes nothing to the ledger**. The
agent can no longer write a meal at all — it only parses free text / receipts
into a draft.

**Flow:**

1. User: `@bot 500k ăn trưa cả nhóm trừ An`.
2. Timeline streams the parse. Agent calls `propose_meal`, returning a draft:
   `{ payer_member_id, member_participants[], guests[], bill_total,
   adjustments[], dish?, initiator?, note?, raw_input, per_head_preview }`.
3. Backend, **before** posting the new draft, **auto-commits any existing
   pending draft** in the room (the "only when superseded" rule). It then posts a
   `kind="expense_draft"` message (persisted, `status:"pending"`). **At most one
   pending draft per room at any time.**
4. Frontend renders an **interactive draft card**:
   - **Payer** selector (room members; defaults to sender if unspecified).
   - **Member chips** — tap to include/exclude; included = billed.
   - **"+ Thêm khách"** — free-text name → removable guest chip; counts toward
     head-count, never billed.
   - **Amount** (editable) and **adjustments** (per-member ±, under an "advanced"
     toggle).
   - **Story fields** — optional `dish`, `initiator`, free `note` inputs,
     pre-filled from parse.
   - Live **"tạm tính"** per-head preview (client-side, labelled provisional).
   - Actions: **Ghi ngay** (commit now), **Huỷ** (cancel — will not be saved),
     or just edit.
5. **Edits PATCH the draft** (debounced) so the server-side record always
   reflects the latest visible state. Thus auto-save-on-supersede saves the
   *edited* draft, and "no feedback" saves the proposal as-is.
6. **Commit** (explicit **Ghi ngay** or supersede) calls one shared
   `commit_draft()`: validates → `ledger.record_meal` under `_agent_lock` → posts
   the server-rendered meal message (with the balance table, §4.5) → flips the
   draft's `status` → `committed`. **Huỷ** flips `status` → `cancelled` (never
   written to the ledger).
7. A lone, un-superseded draft remains `pending` (editable) until the next lunch.

**Who may act:** any room member can edit/commit/cancel the pending draft
(shared, high-trust room). On commit, validation runs against the live ledger;
last-write-on-commit wins.

**Draft lifecycle states:** `pending` → (`committed` | `cancelled`). Committed
and cancelled drafts render as read-only cards in history.

### 4.4 Occasional-guest money model (guest-pays-cash)

New pure function `money.split_with_guests(total, member_ids, guest_count,
adjustments, payer_id)`:

- per-head is computed over **members + guests** (so the number the group sees is
  correct);
- members are billed their per-head (+ adjustments); **guest shares are dropped**
  (assumed settled in cash at the table);
- the persisted meal has `total_amount = Σ member shares` (= bill − guest total)
  and `participants = members only`; **guest names are stored** in a nullable
  `guests` JSON column on `Meal` for audit and display.

**Why this is correct:** payer.paid = (bill − guest_total); each member.consumed
= their per-head share; the shares sum to (bill − guest_total). So
payer.balance = what the payer is owed by *members only* — exactly right, because
the guest cash already settled the guest portion offline. **Settlement and QR are
untouched** (guests are never members, never appear in transfers).

**Display trust:** the draft/meal payload carries **both** `bill_total` (the
500k the user typed) and the tracked member portion, and the card shows "gồm N
khách trả tiền mặt", so the visible total always matches what the user said even
though the ledger tracks only the member portion.

**Rounding:** the integer-division remainder is assigned to the payer (a member,
who is always a participant here) per the existing `split_shares` rule, so
remainder stays inside the tracked (member) portion.

### 4.5 Balance table after logging (gap 5)

`commit_draft()` also computes `ledger.period_balances(last_settlement_to →
today_ict())` for the room and attaches a **full per-person paid / consumed /
balance table** to the meal message, sorted by balance descending (who is owed
most at the top). A new `<BalanceTable>` sub-component in `bot-message.tsx`
renders it under the meal card.

### 4.6 Data model & API surface

**Models (`backend/app/models.py`):**
- `RoomMessage.kind` gains `"expense_draft"`.
- `Meal` gains nullable `dish`, `initiator`, and `guests` (JSON list of names)
  columns (`note`, `raw_input` already exist).

**Message attachments:**
- `expense_draft` — `{ type:"expense_draft", status, draft_id, payer_member_id,
  member_participants[], guests[], bill_total, adjustments[], dish, initiator,
  note, per_head_preview }`.
- `meal` (committed) — existing shape + `dish`, `initiator`, `note`, `guests[]`,
  `bill_total`, and `balances[]` (the table).

**Backend:**
- `backend/app/agui.py` — lean AG-UI adapter (ported from reference).
- `run_turn` — streaming hook publishing `agent.*` events during the turn.
- `propose_meal` tool (replaces `record_meal` in the toolset); flushes prior
  pending draft is handled at the chat/route layer, not the tool.
- `money.split_with_guests` — new pure function.
- `ledger.record_meal` — accept guests (count + names) and compute via
  `split_with_guests`.
- `commit_draft()` (in `chat.py`) — shared commit path for explicit + supersede.
- Endpoints (`main.py`):
  - `PATCH /api/rooms/{id}/drafts/{draft_id}` — persist edits / set
    `status:"cancelled"`.
  - `POST /api/rooms/{id}/meals` — explicit commit-now of a draft.
  - Expose `bot_handle` (room-info or members payload) for the `@` dropdown.
- `settle_period` — **unchanged**.

**Frontend:**
- `<AgentTimeline>`, `<ExpenseDraftCard>`, `<BalanceTable>` components.
- Composer: `@`-dropdown + optimistic echo.
- `mergeEvent` extensions for `agent.*` events and draft status transitions.
- `api.ts`: draft PATCH, commit POST, config/bot-handle fetch.

---

## 5. Build order (single spec, phased plan)

- **Phase A — Live timeline + optimistic echo** (gaps 1, 2). Foundation:
  `agui.py`, `run_turn` streaming, `mergeEvent`/`<AgentTimeline>`, optimistic
  echo.
- **Phase B — `@bot` dropdown** (gap 3). Small, independent.
- **Phase C — Expense draft + guests + commit** (gap 4 + auto-save + metadata).
  The bulk: `propose_meal`, `split_with_guests`, `ledger` guest support,
  `commit_draft`, draft endpoints, `<ExpenseDraftCard>`.
- **Phase D — Balance table** (gap 5). Depends on C.

---

## 6. Testing

### 6.1 Full unit coverage (new + changed code)

**`money.split_with_guests`** (pure, exhaustive):
- equal split, no guests → identical to `split_shares`;
- with guests: per-head over (members + guests), member shares sum to
  (bill − guest_total);
- remainder lands on the payer (member); member shares still sum correctly;
- adjustments among members apply on top of the guest-adjusted per-head;
- error cases: `total <= 0`, no members, adjustment names a non-member,
  Σ adjustments > tracked total, any resulting share < 0.

**`ledger.record_meal` (guest support):** persists `total_amount = Σ member
shares`, `participants = members only`, guest names stored; `period_balances`
after a guest meal nets to the member-only expectation.

**`propose_meal` tool:** returns a draft and performs **zero** ledger writes
(assert no `Meal`/`MealShare` rows created); pre-fills `dish`/`initiator`/`note`
from parsed args.

**`commit_draft` / commit endpoint:** writes the meal, returns balances, flips
draft `status` → `committed`; supersede path auto-commits the prior pending draft
and enforces the single-pending-draft invariant; cancel path writes nothing and
sets `status:"cancelled"`.

**AG-UI adapter (`agui.py`):** message → event mapping (assistant text →
`agent.text.delta`; `tool_call` running/completed → `agent.tool.start`/`.result`;
status ERROR → `agent.run.error`); tool-name/args/result unwrapping for the
`name=='mcp'` envelope; open tools closed on interrupt.

**Frontend:** `@`-dropdown open/filter/keyboard/accept; optimistic echo
append + reconcile + error; `mergeEvent` for each `agent.*` event and draft
status transitions; `<ExpenseDraftCard>` provisional per-head recompute on
toggle; commit/cancel/edit calls.

### 6.2 Golden dataset (scenario fixtures)

A checked-in fixture set (`backend/tests/golden/meals.json` or parametrized
cases) of **input → expected output**, asserted end-to-end through
`propose_meal` → `commit_draft` → `period_balances`. Room: members
An(1), Bình(2), Cường(3), Dung(4). Amounts in integer VND.

| # | Scenario | Input | Expected (shares / balances) |
|---|----------|-------|------------------------------|
| G1 | Even split, all members | payer=An, participants=[An,Bình,Cường,Dung], total=400000 | each share 100000; An.balance +300000; others −100000 |
| G2 | Exclude a member ("trừ An") | payer=Bình, participants=[Bình,Cường,Dung], total=300000 | each 100000; Bình +200000; Cường/Dung −100000; An 0 |
| G3 | Payer not a participant | payer=An, participants=[Bình,Cường], total=200000 | Bình/Cường 100000 each; An +200000; Bình/Cường −100000 |
| G4 | Adjustment (Bình +50k) | payer=An, participants=[An,Bình], total=250000, adj={Bình:+50000} | base=(250000−50000)/2=100000 → An 100000, Bình 150000; An +150000, Bình −150000 |
| G5 | Remainder to payer | payer=An, participants=[An,Bình,Cường], total=100000 | base=33333; remainder 1 → An 33334, Bình 33333, Cường 33333; sum=100000 |
| G6 | **1 guest pays cash** | payer=An, members=[An,Bình,Cường], guests=["Emi"], bill=400000 | per-head over 4 = 100000; tracked total=300000 (members only); An.paid=300000, each member.consumed=100000; An +200000, Bình/Cường −100000; **Emi never in ledger**; card shows bill 400000 + "gồm 1 khách" |
| G7 | 2 guests pay cash | payer=Bình, members=[Bình,Cường], guests=["X","Y"], bill=400000 | per-head over 4 = 100000; tracked total=200000; Bình.paid=200000; Bình +100000, Cường −100000 |
| G8 | Guest + remainder stays on payer | payer=An, members=[An,Bình], guests=["Z"], bill=100000 | per-head over 3 = 33333, remainder 1 → payer; tracked total = An.share + Bình.share (members) with remainder on An; assert Σ member shares == tracked total and guest excluded |
| G9 | Supersede auto-commit | draft D1 pending (G1); `propose_meal` creates D2 (G2) | D1 auto-committed with its persisted state before D2 posts; exactly one pending draft (D2); balances reflect both meals |
| G10 | Cancel writes nothing | draft pending → PATCH status=cancelled | no `Meal` rows; `status:"cancelled"`; balances unchanged |
| G11 | Edit-then-supersede saves edits | draft pending, PATCH removes Cường, then superseded | committed meal reflects the edited participant set, not the original proposal |
| G12 | Metadata round-trip | `propose_meal` parses "phở, Emi rủ"; commit | committed meal has `dish="phở"`, `initiator="Emi"`, `raw_input` = original; non-money fields never affect shares |

Each golden case asserts: (a) per-member shares, (b) `total_amount` persisted,
(c) `period_balances` net, (d) guests excluded from ledger, (e) Σ shares ==
tracked total. G9–G12 also assert draft lifecycle.

---

## 7. Risks & mitigations

- **Full timeline noise.** Mitigated by auto-collapse after `agent.run.finished`.
- **Client preview vs server truth drift.** The per-head preview is client-side
  and labelled "tạm tính"; authoritative numbers are recomputed server-side on
  commit and rendered from the result — drift affects only the transient preview,
  never the ledger.
- **Concurrent draft edits (multi-client).** Last-write-on-commit wins; commit
  validates against the live ledger. Acceptable for a small, high-trust room.
- **Timeline not persisted.** Live-only; reload falls back to the final message.
  Documented and intended.
- **Single-worker lock.** `_agent_lock` and the single-pending-draft invariant
  assume one uvicorn worker (already required by the ledger single-writer design).
