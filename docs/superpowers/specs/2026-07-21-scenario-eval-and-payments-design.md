# Design: week-long scenario eval + payments + revised draft lifecycle

Date: 2026-07-21 (revised 2026-07-22)

## Goal

A reusable **behavioral eval** that plays a realistic week of lunch-splitting
chat through the bot and verifies, at every step, the money outcomes (balances,
who-owes-who, QR) and the bot-visible responses. Building it surfaces work in
three areas:

1. **Ad-hoc payments** between members (a real capability gap) — full feature.
2. **A revised draft lifecycle** (a deliberate design change): proposals are no
   longer auto-committed when superseded; they persist as editable cards,
   settling is gated on open proposals, and approved meals can be corrected.
3. **The hybrid eval harness** itself.

## Hybrid eval runners

Two runners over one shared scenario spec:

1. **Deterministic engine eval** (CI default) — drives the *real* money engine
   (drafts → ledger → payments → settle) and the *real* server-side response
   rendering, clock frozen per day. Asserts state after each step. The correct
   tool calls are encoded in the spec; the LLM is not involved.
2. **Opt-in LLM eval** (skipped unless `RUN_LLM_EVAL=1`) — replays each user
   message through `agent.run_turn` against Cursor and asserts the bot selected
   the expected tool(s) with matching args. Non-deterministic; out of CI.

## Design change: draft lifecycle

Today `drafts.create_draft` auto-commits the prior pending draft on supersede
(at most one pending draft per room). We replace this with a persist-and-confirm
model:

1. **No supersede-autocommit.** Creating a new proposal never commits an
   existing one. Multiple `pending` drafts can coexist; each is confirmed,
   edited, or cancelled independently from its card. `create_draft` returns
   `(new_draft, [])` — the `extras` supersede artifacts are gone.
   `get_pending_draft` → **`list_pending_drafts`** (all pending, oldest first).
2. **Settle gate (block & ask).** `settle_period` first checks for pending
   drafts. If any exist it does **not** compute or commit — it returns
   `{ok, type:"settle_blocked", pending:[…], message}` listing them, and the bot
   asks the user to confirm or cancel them first. Applies to previews and to
   `commit:true` (reset). After the user resolves the pending drafts, a re-run
   settles.
3. **Edit an approved meal (void + re-record).** A committed card can be
   re-opened and edited. Saving voids the old meal and records a new one from
   the edited fields (ledger stays append-only; the card's `committed_meal_id`
   is repointed). **Guard:** if the meal is already inside a committed
   settlement (`meal.occurred_on <= last_settlement.period_to`), the edit is
   rejected — the closed period's netted numbers/QRs must not shift; the bot/UI
   tells the user to record a correcting entry instead.

Money-safety (design D3) is preserved throughout: amounts flow user → LLM → tool
once; the tools own every number; visible money bodies render server-side from
tool-result dicts.

## The scenario (ground truth)

Members `a1,a2,a3,a4` (+`a5` added Thursday). One open period (no committed
settlement until step 11), so balances are cumulative `paid − consumed`, plus
payment adjustments. Guest meals persist the **tracked (member) total**, not the
bill. Under the revised lifecycle, each logged meal is **explicitly confirmed**
(nothing auto-commits); the Thursday meal is deliberately left pending.

| # | Day | Message | Bot action | Effect |
|---|-----|---------|------------|--------|
| 1 | Mon | a1 "I pay 300k" | ask who → `propose_meal` [a1,a2,a3,a4]; user confirms | M1: a1 pays 300k, ÷4 = 75k |
| 2 | Tue | a1 "I pay 150k, a4 out" | `propose_meal` [a1,a2,a3]; confirm | M2: a1 pays 150k, ÷3 = 50k |
| 3 | Tue | a1 "received 125k from a2" | `record_payment` a2→a1 125k | a2 +125, a1 −125 |
| 4 | Wed | a2 "paid 500k, all 4 + 1 guest" | `propose_meal` guests=["guest1"]; confirm | M3: tracked 400k, a2 paid 400k, head ÷5 = 100k |
| 5 | Wed | a3 "how much do I pay?" | `settle_period` commit:false (no pending) → QR | a3 → a2 225k (QR) |
| 6 | Thu | a4 "add a5" | `add_member` | roster gains a5 |
| 7 | Thu | a4 "paid 400k, a2 out" | `propose_meal` [a1,a3,a4,a5]; **left pending** | Draft D_T (÷4 = 100k), uncommitted |
| 8 | Fri | a5 "tính tiền" | `settle_period` → **settle_blocked** (D_T pending) | no settle; bot asks to confirm D_T |
| 9 | Fri | a1 "paid 300k for all" | confirm D_T (→ M_T); `propose_meal` [all 5] → D_F pending | M_T committed (dated Fri); D_F pending |
| 10 | Fri | a5 "tính tiền" | confirm D_F (→ M4); `settle_period` commit:false | full transfers + QR |
| 11 | Fri | a1 "trả đủ rồi, reset balance" | `settle_period` commit:true (no pending) | period closes |
| 12 | next Mon | a1 "còn ai nợ ai không?" | `settle_period` since_last → empty | "no one owes anything" |

Step 8 blocks rather than silently excluding the draft (the chosen "block &
ask"). Step 9's "save previous draft" is now an **explicit confirm** of D_T (no
auto-commit), and D_F stays pending until step 10 confirms it.

### Computed balances

Paid uses the **tracked** meal total (M3 → a2 paid 400k; the guest's 100k is
cash). Payment P1 (a2→a1 125k): `balance[a2] += 125k`, `balance[a1] -= 125k`.

**At step 10** (M1,M2,M3,M_T,M4 + P1 all committed, before reset):

| | a1 | a2 | a3 | a4 | a5 |
|---|----|----|----|----|----|
| balance | +240k | +240k | −385k | +65k | −160k |

Greedy netting (`money.net_transfers` re-selects the max debtor *and* max
creditor every iteration; ties by lowest id):
`a3→a1 240k`, `a5→a2 160k`, `a3→a2 80k`, `a3→a4 65k` (sums to 0; a3 pays 385k,
a5 pays 160k). The edges are not "one debtor drained at a time" — after a3's
first transfer, a5 (160k) outranks a3's remainder (145k), so the algorithm
jumps to a5.

**At step 5** (only M1,M2,M3 + P1 committed):

| | a1 | a2 | a3 | a4 |
|---|----|----|----|----|
| balance | +100k | +300k | −225k | −175k |

Netting: `a3→a2 225k`, `a4→a1 100k`, `a4→a2 75k`. Step 5 (a3's question) surfaces
a3's slice: `a3→a2 225k`.

**At step 12** (since_last, after reset): no meals/payments in window → empty
transfers → "mọi người đã cân bằng".

### Simulation assumptions

- `settle_period` reads only committed `Meal`/`Payment` rows via
  `period_balances`; a pending draft (a `RoomMessage`) is invisible to it. So an
  unconfirmed proposal is both (a) what the settle gate reports, and (b) absent
  from any computed balance.
- The superseded draft D_T commits at step 9 **dated Friday** — draft
  attachments carry no `occurred_on`, so `commit_draft`→`record_meal` stamps the
  then-current day. Balances/transfers are unaffected (one open window spans
  Thu+Fri); the eval must NOT assert Thursday for M_T.
- "reset balance" (step 11) = `settle_period commit:true`; the prompt must route
  "trả đủ rồi / reset" here and NOT to `record_payment` (which would create a
  fresh imbalance atop the closed period).

## Feature: ad-hoc payments

A member paying another back in cash, outside a meal or period-closing
settlement. Load-bearing: without it a2/a1 are off by 125k and every downstream
transfer changes.

- **Model `Payment`** — `room_id, from_member_id, to_member_id, amount,
  occurred_on, note, source, logged_by, voided, voided_by, voided_at,
  created_at`. Append-only, mirrors `Meal`. Picked up by `Database.create_all`.
- **`ledger.record_payment(...)`** — validates both members belong to the room
  (`room_id` only, not `active`, mirroring meals), `from != to`, `amount > 0`;
  writes one row; returns a summary dict.
- **`ledger.period_balances`** — fold payments **after** the `balance =
  paid − consumed` loop (folding before is overwritten by that loop):
  `balance[from] += amount`, `balance[to] -= amount`, with `setdefault` for
  members whose only window activity is a payment; voided payments excluded. This
  breaks the `balance == paid − consumed` identity once payments exist (no
  renderer/test relies on it — verified against current code).
- **Tool `record_payment`** — `{from, to, amount}` (member ids; `from` defaults
  to sender). Returns `{ok, type:"payment", from:{id,name}, to:{id,name},
  amount, balances}` (balances via `drafts.current_balances`).
- **Prompt** — a "Ghi trả tiền mặt" section: "X trả/đưa Y N" or "tôi nhận N từ
  Y" → `record_payment`; and a "Chốt/reset" note routing "trả đủ rồi / reset" to
  `settle_period commit:true`.
- **Rendering** — `chat._payment_body` renders "💸 Đã ghi: «A» trả «B» N đ" from
  the tool dict; `render_bot_attachments` dispatches `record_payment` →
  `{type:"payment", …}`; **`run_bot_turn` gets a `payment` branch** in its body
  dispatch (chat.py ~228) so the amount never renders from LLM prose.
  Precedence: a turn with both a settle and a payment result renders the
  payment (a payment turn shouldn't also settle).

## Feature: edit-approved (void + re-record)

- **`drafts.recommit_draft(session, draft_id, room_id, patch, logged_by)`** —
  draft must be `committed` with a `committed_meal_id`; the linked meal must be
  un-voided and **not settled** (`last_settlement` with
  `period_to >= meal.occurred_on` → `LedgerError`). Applies `patch` (the
  `_EDITABLE` fields) to the draft attachments, voids the old meal, records a new
  meal, repoints `committed_meal_id`, returns the new meal `RoomMessage`.
- **Route** `POST /api/rooms/{room_id}/drafts/{draft_id}/recommit` — body is the
  full edited draft fields; runs under `chat._agent_lock`; 409 on
  `LedgerError`/`MoneyError` (incl. the settled guard). Publishes the updated
  draft + new meal messages.
- No new LLM tool: the bot's own correction path stays `void_meal` +
  `propose_meal`.

## Feature: settle gate

- **`drafts.list_pending_drafts(session, room_id)`** → pending draft rows,
  oldest first.
- **`tools.settle_period`** — at the top, if `list_pending_drafts` is non-empty,
  return `{ok:True, type:"settle_blocked", pending:[{draft_id, payer_name,
  bill_total, participant_count}], message:"Có N đề xuất chưa xác nhận — xác nhận
  hoặc huỷ trước khi chốt."}` without touching balances. `commit:true` blocks
  the same way.
- **Rendering** — `chat._settle_blocked_body` lists the pending proposals and
  the ask; `render_bot_attachments` maps `settle_blocked`; `run_bot_turn` renders
  it via a branch (like settlement).

## Frontend

- **`expense-draft-card.tsx`** — a committed (`status==="committed"`) card gains
  an **Edit** button that flips a local `editing` flag, re-enabling the fields
  (currently gated by `readonly = status !== "pending"`; becomes
  `readonly = status !== "pending" && !editing`). While editing: **Save changes**
  (calls the new `recommit` API) and **Cancel edit** (reverts local state). A 409
  (settled) surfaces in the existing `error` line. Pending-card behavior
  unchanged.
- **`lib/api.ts`** — add `recommitDraft(roomId, draftId, fields)` POSTing to the
  new route.
- Cancelled cards remain read-only (no edit).

## Harness architecture

- **`tests/golden/scenario_week.py`** — the declarative spec: `MEMBERS` (with
  bank details for payees), and an ordered `STEPS` list where each step is
  `{day, actor, kind, …args, expect}`. `kind` ∈ `{meal_confirmed, payment,
  add_member, leave_pending, confirm_pending, settle, settle_commit}`.
  `meal_confirmed` = create draft + commit; `leave_pending` = create draft only;
  `confirm_pending` = commit a named pending draft. `expect` carries
  `balances`, `transfers`, `qr_payees`, `blocked_pending`, or `empty` as
  applicable. Member indices are 1-based, resolved to ids by the runner.
- **Deterministic runner `tests/test_scenario_week.py`** (CI):
  - Seeds a room with bank details for a1, a2, a4 (every payee across the
    scenario). Reuses/extends `test_ledger._seed_room`.
  - Iterates STEPS. For each, monkeypatches **`app.clock.now_ict`** to the
    step's day at noon ICT (NOT `today_ict`: `ledger`/`drafts`/`tools` bind
    `today_ict` at import via `from app.clock import today_ict`, so patching that
    attribute is ineffective; but `today_ict`'s body resolves `now_ict` through
    `app.clock` globals at call time, so patching `now_ict` reaches all callers).
  - Executes the step against the real modules (`drafts`/`ledger`/`tools`/`chat`)
    and asserts:
    - `ledger.period_balances(...)` equals `expect.balances`;
    - settle steps: `tools.settle_period` transfers equal `expect.transfers`,
      every payee row has a non-null `qr_url`, and the rendered
      `chat._settlement_body` contains the expected amounts;
    - blocked settle steps: result `type == "settle_blocked"` and `pending`
      lists the expected draft(s);
    - payment/meal steps: the rendered `_payment_body`/`_meal_body` contains the
      expected amounts.
  - Assertion helpers are keyed off the spec so the scenario is data, not code.
- **LLM runner `tests/test_scenario_week_llm.py`** (opt-in):
  - `pytestmark = pytest.mark.skipif(not os.getenv("RUN_LLM_EVAL"), …)`.
  - Replays each step's `message` through `run_turn`; asserts `TurnResult.tools`
    contains the expected tool name and key args. Tolerant of extra tool calls
    (e.g. `find_members`) and of VN/EN prose.

## Testing

TDD throughout:

- `test_ledger` — payment balance folding, validation (`from!=to`, `amount>0`,
  unknown/cross-room member), voided-payment exclusion; `recommit_draft`
  happy path + settled-guard.
- `test_tools` — `record_payment` happy path + errors; `settle_period` gate
  (blocked when a pending draft exists; settles after it's confirmed/cancelled).
- `test_chat` — `_payment_body`, `_settle_blocked_body`, `render_bot_attachments`
  dispatch, and the `run_bot_turn` body branches.
- `test_golden_meals` — **replace** G9 (supersede-autocommit) and G11
  (edit-then-supersede) with new-lifecycle tests: creating a second draft leaves
  the first `pending` (no auto-commit); two drafts coexist pending. G10 (cancel
  writes nothing) stays.
- `test_drafts` / API test — `recommit` route (200 + new meal_id; 409 when
  settled); settle-gate integration.
- `test_scenario_week` — the full deterministic walkthrough (the eval).
- Frontend: `expense-draft-card` test — committed card shows Edit; editing
  re-enables fields and calls `recommit`; settled-guard error renders.
- Existing suites stay green except the intentionally-replaced supersede tests
  and any test asserting the old auto-commit behavior (audited and updated).

## Out of scope

- A dedicated zero-out reset (reset = settle commit).
- An LLM-facing `edit_meal`/`void_payment` tool (correction via `void_meal` +
  `propose_meal`; payment void exists at the ledger layer for tests only).
- Editing a meal that's inside a closed settlement (explicitly blocked).
