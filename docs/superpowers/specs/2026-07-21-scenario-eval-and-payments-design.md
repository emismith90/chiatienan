# Design: week-long scenario eval + ad-hoc payments

Date: 2026-07-21

## Goal

A reusable **behavioral eval** that plays a realistic week of lunch-splitting
chat through the bot and verifies, at every step, the money outcomes (balances,
who-owes-who, QR) and the bot-visible responses. Building it surfaces one real
capability gap — **ad-hoc payments between members** — which we implement as a
full feature so the scenario is true end-to-end.

Two runners over one shared scenario spec (hybrid):

1. **Deterministic engine eval** (CI default) — drives the *real* money engine
   (drafts → ledger → payments → settle) and the *real* server-side response
   rendering, with the clock frozen per day. Asserts state after each step. Does
   not exercise the LLM; the correct tool calls are encoded in the spec.
2. **Opt-in LLM eval** (skipped unless `RUN_LLM_EVAL=1`) — replays each user
   message through `agent.run_turn` against Cursor and asserts the bot selected
   the expected tool(s) with matching args. Non-deterministic; out of CI.

## The scenario (ground truth)

Members `a1,a2,a3,a4` (+`a5` added Thursday). One open period (no committed
settlement until step 11), so balances are cumulative `paid − consumed`, plus
payment adjustments. Guest meals persist the **tracked (member) total**, not the
bill (guest pays cash).

| # | Day | Message | Bot action | Effect |
|---|-----|---------|------------|--------|
| 1 | Mon | a1 "I pay 300k" | ask who → `propose_meal` [a1,a2,a3,a4], confirm | M1: a1 pays 300k, ÷4 = 75k |
| 2 | Tue | a1 "I pay 150k, a4 out" | `propose_meal` [a1,a2,a3], confirm | M2: a1 pays 150k, ÷3 = 50k |
| 3 | Tue | a1 "received 125k from a2" | `record_payment` a2→a1 125k | a2 +125, a1 −125 |
| 4 | Wed | a2 "paid 500k, all 4 + 1 guest" | `propose_meal` guests=["guest1"], confirm | M3: tracked 400k, a2 paid 400k, head ÷5 = 100k |
| 5 | Wed | a3 "how much do I pay?" | `settle_period` commit:false → QR | a3 → a2 225k (QR) |
| 6 | Thu | a4 "add a5" | `add_member` | roster gains a5 |
| 7 | Thu | a4 "paid 400k, a2 out" | `propose_meal` [a1,a3,a4,a5], leave **pending** | Draft T (÷4 = 100k), uncommitted |
| 8 | Fri | a5 "tính tiền" | `settle_period` commit:false, **excludes** draft T | transfers over M1–M3 |
| 9 | Fri | a1 "paid 300k for all" | `propose_meal` [all 5] → supersede-commits draft T; confirm new draft | M_T committed; M4: a1 pays 300k ÷5 = 60k |
| 10 | Fri | a5 "tính tiền" | `settle_period` commit:false, includes Thu+Fri | full transfers |
| 11 | Fri | a1 "trả đủ rồi, reset balance" | `settle_period` commit:true | period closes |
| 12 | next Mon | a1 "còn ai nợ ai không?" | `settle_period` since_last → empty | "no one owes anything" |

### Computed balances

Paid uses the **tracked** meal total (M3 → a2 paid 400k, the guest's 100k is
cash). Payment P1 (a2→a1 125k) does `balance[a2] += 125k`, `balance[a1] -= 125k`.

**At step 10** (all six writes committed, before reset):

| | a1 | a2 | a3 | a4 | a5 |
|---|----|----|----|----|----|
| balance | +240k | +240k | −385k | +65k | −160k |

Greedy netting (`money.net_transfers` re-selects the max debtor *and* max
creditor every iteration; ties by lowest id):
`a3→a1 240k`, `a5→a2 160k`, `a3→a2 80k`, `a3→a4 65k` (sums to 0; a3 pays 385k,
a5 pays 160k). Note the edges are not "one debtor fully drained at a time" — the
algorithm jumps to a5 after a3's first transfer because a5 (160k) then outranks
a3's remainder (145k).

**At step 5 / step 8** (only M1,M2,M3 + payment P1 committed):

| | a1 | a2 | a3 | a4 |
|---|----|----|----|----|
| balance | +100k | +300k | −225k | −175k |

Netting: `a3→a2 225k`, `a4→a1 100k`, `a4→a2 75k`. (Step 5 is a3's slice of this:
`a3→a2 225k`.)

**At step 12** (since_last, after reset): no meals in window → empty transfers →
"mọi người đã cân bằng".

### Simulation assumptions

- Step 9's new a1 draft is **confirmed** before step 10, so Friday's meal is in
  the settlement (matches "second tính tiền includes thursday and friday"). This
  is necessary, not stylistic: `settle_period` reads only committed `Meal` rows
  via `period_balances`; a pending draft (a `RoomMessage`) is invisible to it.
  The deterministic runner confirms it explicitly (`confirm_pending`).
- The superseded draft T (step 7) commits at step 9 **dated Friday** — draft
  attachments carry no `occurred_on`, so `commit_draft`→`record_meal` stamps it
  with the then-current day. Balances/transfers are unaffected (one open window
  spans Thu+Fri), but the eval must NOT assert Thursday for M_T.
- "reset balance" (step 11) = `settle_period commit:true`: it records the
  outstanding transfers and advances the `since_last` baseline; forward balances
  read zero. No dedicated zero-out tool. The prompt guidance must steer the LLM
  to route "trả đủ rồi / reset" here — and NOT to record it as a `record_payment`
  (that would create a fresh imbalance on top of the closed period).

## New feature: ad-hoc payments

A member paying another back in cash, outside a meal or a period-closing
settlement. Load-bearing: without it a2/a1 are off by 125k and every downstream
transfer changes.

- **Model `Payment`** — `room_id, from_member_id, to_member_id, amount,
  occurred_on, note, source, logged_by, voided`. Append-only, mirrors `Meal`.
- **`ledger.record_payment(...)`** — validates both members belong to the room,
  `from != to`, and `amount > 0`; writes one row; returns a summary dict. Member
  check mirrors meals (`room_id` only, not `active`), so a payment can involve a
  since-removed member.
- **`ledger.period_balances`** — fold payments **after** the `balance =
  paid − consumed` loop (folding before would be overwritten by that loop):
  `balance[from] += amount`, `balance[to] -= amount`, with `setdefault` for
  members whose only window activity is a payment. Payments carry no
  `paid`/`consumed`; they adjust `balance` directly. Voided payments excluded.
  Note this breaks the `balance == paid − consumed` identity once payments
  exist — confirm no renderer/test relies on it.
- **Tool `record_payment`** — `{from, to, amount}` (member ids; `from` defaults
  to sender). Returns `{ok, type:"payment", from, to, amount, balances}`.
- **Prompt** — a "Ghi trả tiền mặt" section: "X trả/đưa Y ..." or "tôi nhận N từ
  Y" → `record_payment`. Amounts pass through once (money-safety D3 holds: the
  tool owns the number).
- **Rendering** — `chat.render_bot_attachments` recognizes `record_payment`
  (define precedence if a turn produced both a settle and a payment result);
  `_payment_body` renders a deterministic VN line ("💸 Đã ghi: «A» trả «B»
  N đ") from the tool dict. Crucially, add a `payment` branch to
  `run_bot_turn`'s body dispatch (chat.py ~228, alongside the `settlement`
  branch) — otherwise the amount renders from the LLM's `final_text`, violating
  money-safety. Reset/settle rendering unchanged.

Money-safety unchanged: the amount flows user → LLM → tool once; the tool owns
it thereafter.

## Harness architecture

- **`tests/golden/scenario_week.py`** — the declarative spec: `MEMBERS`, and an
  ordered `STEPS` list where each step is `{day, actor, kind, ...args, expect}`.
  `kind` ∈ `{meal, meal_pending, confirm_pending, payment, add_member, settle,
  settle_commit}`. `expect` carries expected `balances` and/or `transfers` and
  QR expectations. Member indices are 1-based, resolved to ids by the runner.
- **Deterministic runner `tests/test_scenario_week.py`** (CI):
  - Seeds a room (extending `test_ledger._seed_room`) with **bank details for
    every payee** (a1, a2, a4) — `_seed_room` seeds none, and `make_qr_url`
    raises `QRError` (→ `qr_url: None` + a warning) for a payee without a bank,
    which would fail the QR assertion.
  - Iterates STEPS. For each, monkeypatches **`app.clock.now_ict`** to the
    step's day (NOT `app.clock.today_ict`: `ledger`/`drafts`/`tools` bind
    `today_ict` at import via `from app.clock import today_ict`, so patching the
    attribute is ineffective; but `today_ict`'s body resolves `now_ict` through
    `app.clock` globals at call time, so patching `now_ict` propagates to all of
    them). Then executes the step against the real modules
    (`drafts`/`ledger`/`chat`), then asserts:
    - `ledger.period_balances(...)` equals `expect.balances`;
    - for settle steps, `tools.settle_period(...)` transfers equal
      `expect.transfers` and every payee row has a non-null `qr_url`;
    - the rendered bot body (via `chat._settlement_body` / `_meal_body` /
      `_payment_body`) contains the expected amounts.
  - Assertion helpers are keyed off the spec so the scenario is data, not code.
- **LLM runner `tests/test_scenario_week_llm.py`** (opt-in):
  - `pytestmark = pytest.mark.skipif(not os.getenv("RUN_LLM_EVAL"), ...)`.
  - Replays each step's `message` through `run_turn`; asserts the resulting
    `TurnResult.tools` contains the expected tool name and key args. Tolerant of
    extra tool calls (e.g. `find_members`) and of VN/EN prose.

## Testing

TDD throughout:

- `test_money`/`test_ledger` — payment balance folding, validation, void.
- `test_tools` — `record_payment` happy path + errors (unknown member, amount
  ≤ 0, cross-room).
- `test_chat` — `_payment_body` rendering + `render_bot_attachments` dispatch.
- `test_scenario_week` — the full deterministic walkthrough (the eval).
- Existing suites stay green (payments are additive; `period_balances`
  signature unchanged).

## Out of scope

- Frontend rendering of a payment card (backend body only for now).
- Editing/deleting payments via chat (void exists at the ledger layer for tests;
  no `void_payment` tool yet).
- The dedicated zero-out reset (reset = settle commit).
