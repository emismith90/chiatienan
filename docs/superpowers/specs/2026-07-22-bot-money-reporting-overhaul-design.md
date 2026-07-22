# chiatienan — bot money-reporting overhaul

- **Date:** 2026-07-22
- **Status:** Approved (design), pending implementation plan
- **Related:** [`2026-07-20-chiatienan-pwa-design.md`](2026-07-20-chiatienan-pwa-design.md), [`2026-07-20-chat-ux-overhaul-design.md`](2026-07-20-chat-ux-overhaul-design.md)
- **Money-safety anchor:** design D3 — the tools own every number; the model never computes or transcribes an amount that lands in the ledger or a QR.

## 1. Problem

Reading the production room `12B +2 🐔` on 2026-07-22 surfaced four defects in how the bot reports money. All four are reproduced from the real ledger that afternoon:

- Meal #2 — *bún bò huế*, **Linh paid 305k**, split 5 (Emi/Giang/Dũng/TrangDinh/Linh) → **61k each**.
- Meal #3 — *nem nướng + gà rán*, **Giang paid 375k**, split 5 (Trang/Emi/Linh/TrangDinh/Giang) → **75k each**.
- Payments recorded: Emi→Linh 61k, Emi→Giang 75k, TrangDinh→Giang 75k, TrangDinh→Linh 61k, Dũng→Linh 61k.

### ① Personal debt query dumps the whole squad
Dũng: *"@bot tôi còn nợ bao nhiêu, nợ buổi nào và nợ ai?"* → the bot returned the **full team transfer list**, never scoped to Dũng and never naming the meal. Expected: *his* data only — the meals he's in, his share, paid/unpaid, and whom he owes.

### ② A real payment is refused because of netting (the serious one)
Giang: *"toi trả hết cho anh linh r"*. Giang genuinely owes Linh **61k** (his bún-bò share). But `propose_payment`'s pay-off path (`amount` omitted) computes the amount from `per_payer_transfers()`, which **nets opposing debts within the pair**. Since Linh owes Giang 75k (nem nướng) and Giang owes Linh 61k, the net is *Linh → Giang 14k*, so there is no `Giang→Linh` transfer and the tool returns `payment_settled` → bot: *"Giang không còn nợ Linh gì cả — không cần ghi."* Giang's 61k cash payment was **never recorded**; at 16:51 he protested *"sao linh chỉ nợ tôi 14k"*. (Contrast: Dũng said the same words and it worked — Dũng has no opposing credit with Linh, so net == gross.)

### ③ Balance questions default to the group
Every balance question (`how much do I owe`, `current balances`, `show current state`) routed to the whole-group settle view. A person asking about themselves should get their own view by default.

### ④ Summaries aren't chronological, and leak reasoning
*"show summary not detail"* → the bot **hand-built a markdown balance table** (transcribing numbers itself — a money-safety slip — and dropping Linh's & Giang's rows). No timeline of *when* meals happened or *when* people paid. Several replies also leaked internal planning text (*"mình làm theo skill…"*).

## 2. Goals / non-goals

**Goals**
- Personal, sender-scoped statement: what I owe + what I'm owed, **by meal**, with paid/unpaid status (①③).
- Record a stated cash payment against the **gross directional** debt, not the netted pair balance; when a pair owes both ways, **ask** instead of guessing (②).
- Route first-person balance questions to the personal view; keep group/settle explicit (③).
- A **chronological summary card** (timeline of meals + payments) with tool-computed balance bars (④).
- Keep every displayed number tool-owned; stop the reasoning leaks on money turns.

**Non-goals**
- No change to the meal-recording flow, the `settle_period` QR/netting math, or the draft-confirm lifecycle.
- No DB schema change (everything derives from existing `meals` / `meal_shares` / `payments`).
- No historical back-computation of meal *dates* from weekday words ("thứ 3") — out of scope; timeline orders by recorded time. (Noted as a known limitation, §10.)

## 3. Core concept — one primitive under all four

Everything rests on a **gross directional debt breakdown, per meal**, room-scoped over the open period:

> For each meal, every participant other than the payer owes the payer their share. That's a directed edge `debtor → creditor` tagged with `(meal_id, dish, occurred_on, amount)`. Recorded `payments` reduce a pair's outstanding, applied **oldest-meal-first (FIFO)** to derive per-meal paid / partial / unpaid status.

This deliberately does **not** net `A→B` against `B→A`. Netting is only for `settle_period` (minimal QR transfers). The gross-per-direction view is what a human means by "what do I owe Linh" and is exactly the "hint" the agent needs to clarify ② instead of silently cancelling debts.

From this primitive:
- **Personal statement (①③)** = the sender's outgoing edges (grouped by creditor) + incoming edges (grouped by debtor), each with meal + status, plus their net.
- **Group summary (④)** = all meals + all payments as one time-ordered timeline, plus per-member net balances.
- **Pay-off (②)** = outstanding gross `from→to` and `to→from`; one-sided ⇒ auto, two-sided ⇒ clarify.

## 4. Backend design

### 4.1 `money.py` — pure helpers (no I/O)
Add pure functions operating on the same `(meals, payments)` shapes `per_payer_transfers` already takes:
- `gross_debts(meals) -> dict[(debtor, creditor), int]` — sum of shares, **no netting** (extracted from the first loop of `per_payer_transfers`).
- `apply_payments_fifo(edges, payments) -> list[DebtEdge]` — reduce each directional pair's per-meal edges oldest-first; returns edges annotated with `paid`/`outstanding`/`status`.

`per_payer_transfers` stays exactly as-is (settle/QR still nets). Reuse, don't fork.

### 4.2 `ledger.py` — read helpers
- `debt_breakdown(session, room_id, from_date, to_date) -> list[DebtEdge]` — join `meals`+`meal_shares` (exclude voided) into per-`(debtor, creditor, meal)` gross edges with `dish`/`occurred_on`, then fold payments via `apply_payments_fifo`. Same window semantics as `period_balances`.
- `period_timeline(session, room_id, from_date, to_date) -> list[Event]` — meals and payments as a single list ordered by `(occurred_on, created_at, id)`; each event carries a `kind` (`meal`|`payment`) and its display fields.

`period_balances` is unchanged and remains the authoritative per-member net used by the summary's bars.

### 4.3 `tools.py` — tools
**New — `member_statement`** (display-only; ①③)
- Args: `{ member?: int }` (default = `ctx.sender_member_id`), optional period keyword (default `since_last`).
- Returns `{ ok, type:"statement", member:{id,name}, owe:[{creditor,meal,dish,amount,status}], owed:[{debtor,meal,dish,amount,status}], net, period }`.

**New — `get_period_summary`** (display-only; ④)
- Args: period keyword (default `since_last`).
- Returns `{ ok, type:"summary", period, timeline:[…events…], balances:[{id,name,balance}] }` (balances straight from `period_balances`).

**Changed — `propose_payment`** (②). When `amount` is omitted, replace the netted-transfer pay-off with gross-directional logic:
- Compute `gross_ft` = outstanding `from→to` and `gross_tf` = outstanding `to→from` from `debt_breakdown`.
- `gross_ft == 0 and gross_tf == 0` → `payment_settled` (truly nothing owed).
- `gross_ft > 0 and gross_tf == 0` → `payment_draft`, `amount = gross_ft` (unambiguous — Dũng, and Giang→Linh since you can't pay off a negative).
- `gross_ft == 0 and gross_tf > 0` → `type:"nothing_owed"` with a hint that the reverse is what's outstanding (agent explains; does not draft).
- **`gross_ft > 0 and gross_tf > 0` → `type:"payment_ambiguous"`**, returning both candidate transfers computed by the tool:
  - `{ mode:"gross", from, to, amount: gross_ft }` (pay the full directional debt), and
  - `{ mode:"offset", … , amount: |gross_ft − gross_tf| }` in the net direction.
- Explicit `amount` given → unchanged (user stated it once; validate `> 0`, draft it). **Never netted.**

**Money-safety for the clarify round-trip:** `propose_payment` gains a `mode: "gross"|"offset"` arg. The agent re-calls with the chosen *mode*, not a transcribed number — the tool recomputes the amount server-side, so the ledger figure is never carried through the model. The numbers in the agent's *question* are non-authoritative prose.

`settle_period`, `propose_meal`, `find_members`, member CRUD: unchanged. `get_period_balances` is retained (used internally by the summary) but no longer the display path.

### 4.4 `chat.py` — rendering (deterministic bodies)
Extend `render_bot_attachments` to map:
- `member_statement` → `{type:"statement", …}`; `get_period_summary` → `{type:"summary", …}`.
- Add `_statement_body(att)` and `_summary_body(att)` — deterministic Vietnamese text assembled **from the tool dict** (mirrors `_settlement_body`/`_meal_body`), so the visible text can't disagree with the card and no number is model-authored.
- `payment_ambiguous` / `nothing_owed`: no attachment card; the turn ends as a normal `bot` text message (the agent's clarifying question). The subsequent confirmed payment renders as today's `payment_draft` card.

### 4.5 Skills / prompt
- **Rename/retarget `settle-period` skill → `balances`** covering three routes:
  - First-person, no group word (*"tôi nợ ai"*, *"how much do I owe"*, *"my part"*) → `member_statement` (default sender). *(③)*
  - Group summary / state (*"summary"*, *"current state"*, *"tổng kết cả nhóm"*) → `get_period_summary`. *(④)*
  - Settle / close / QR (*"ai trả tuần này"*, *"tạo QR"*, *"chốt"*) → `settle_period` (unchanged).
- **`record-payment` skill:** on `payment_ambiguous`, ASK the user (gross vs offset, showing both amounts) then re-call `propose_payment` with the chosen `mode`. On `payment_settled`, report "không còn nợ" **only** when truly zero. Drop the current net-based shortcut.
- **Prompt:** add a one-liner — answer directly; do **not** narrate skill/tool selection ("mình làm theo skill…"). Fixes the reasoning leak (④).

## 5. Frontend design
`BotMessage` dispatches on `attachments.type`; add two components alongside `SettlementCard`/`MealCard`, reusing `fmt()` and the existing `BalanceTable`:

- **`StatementCard`** (`type:"statement"`) — two sections **Bạn nợ** / **Được nợ**, each row: person · meal (dish + day) · amount · a `paid`/`unpaid` pill; footer shows the net. (Matches the approved mockup, left panel.)
- **`SummaryCard`** (`type:"summary"`) — a vertical **timeline** (meal 🍜 / payment 💸 rows, each dated) plus a compact **net-balance bar** row per member (bars widths from tool-computed `balances`; center line = zero, green right / red left). (Approved mockup, option C.)

Both are theme-aware (existing `--accent`/`--border`/`--bg-*` vars) and mobile-first. New tests mirror `expense-draft-card.test.tsx` / `balance-table.test.tsx`.

## 6. Money-safety analysis
- All displayed amounts come from tool dicts; bodies are server-assembled (§4.4). No hand-built tables.
- The ② clarify loop passes a **mode token**, not an amount, so the recorded figure is tool-derived (D3 "once" preserved).
- Bars are widths derived from `period_balances` on the client for *layout only*; the printed number is the tool's integer.

## 7. Data / migration
None — no schema change. **Live fix for Giang:** after deploy, the fixed flow lets Giang re-send *"tôi trả hết cho anh Linh"* → confirm the 61k gross → payment recorded (Linh then correctly owes Giang 75k). Preferred over a manual row insert; if the group has already moved on, insert one `payments` row (Giang→Linh 61k) directly.

## 8. Testing
- **money.py:** `gross_debts` (no netting), `apply_payments_fifo` (oldest-first, partial, over-payment) — pure unit tests.
- **ledger.py:** `debt_breakdown` (per-meal status incl. the Giang/Linh two-way case), `period_timeline` ordering.
- **tools.py:** `member_statement` scoping/defaults; `get_period_summary` shape; `propose_payment` all five branches (settled / one-sided auto / reverse / ambiguous / explicit) + `mode` recompute; the exact prod fixtures (bún bò + nem nướng + the 5 payments) as a golden scenario.
- **chat.py:** `_statement_body`/`_summary_body` determinism; attachment mapping.
- **frontend:** `StatementCard`, `SummaryCard` render tests.
- TDD throughout (write the failing test first).

## 9. Delivery order
Single spec, but the plan should land ② first (live correctness bug), then the shared primitive (§4.1–4.2), then ①③ statement, then ④ summary, then the prompt/leak polish. Each increment is independently shippable and testable.

## 10. Known limitations / open
- Meal dates aren't parsed from weekday words ("thứ 3/4"); timeline orders by recorded time. Revisit only if it confuses users.
- FIFO payment→meal attribution is a display heuristic; the authoritative figure is per-pair gross − payments. Documented in-code.
