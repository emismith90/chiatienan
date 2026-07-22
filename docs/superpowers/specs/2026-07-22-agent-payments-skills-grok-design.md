# Design — Payments-as-drafts, Cursor skills, grok-4.5

Date: 2026-07-22
Status: Approved (pre-implementation)

## Problem

Diagnosed from the Cursor SDK agent store DBs (per-turn tool calls) plus the code:

1. **The model fabricates payment amounts.** `record_payment` requires an integer
   `amount` ([tools.py](../../../backend/app/tools.py) `_PAYMENT_SCHEMA`), and the
   prompt only shows a single explicit number. When a user says "X đã trả Y" with no
   number, the model invents one (observed 61,000 in some turns, 65,000 in others) —
   violating the bot's own hard rule ("KHÔNG BAO GIỜ tự tính toán… số tiền do người
   dùng nói"). Main source of wrong balances.
2. **No idempotency / no pay-off concept.** "trang đinh cũng trả" recorded a *second*
   identical payment, over-crediting the payer. The settlement then produced a
   nonsensical "Emi → Trang Dinh" transfer (routing money to an over-paid person).
3. **Multi-payer is unreliable.** "Dũng và Giang đã trả Linh" should be two payments;
   the model sometimes dropped one (it apologized for this in a later turn).
4. **`record_payment` writes the ledger directly** — unlike meals, which go through a
   propose→human-confirm draft. Balance-changing actions should require explicit
   confirmation.

## Goals

Four coupled workstreams, one combined spec (per user decision).

1. Payments become a **propose-draft → human-confirm** flow (like meals), and the
   pay-off amount is **computed server-side**, never by the model.
2. Introduce **Cursor "skills"** (workspace `.cursor/rules` + `.cursor/skills`) so the
   bot's procedures live in reliable, versioned skill files instead of a monolithic
   system prompt.
3. Switch the bot model to **grok-4.5 (effort=high, fast=true)**.
4. **Wipe** the corrupted ledger data at deploy.

Non-goals (YAGNI): per-user/org skill tiers, skill fingerprint-cache sophistication,
pairwise-debt storage, editing committed payments (recommit), changing meal flow.

---

## Workstream 1 — Payments as propose-draft → confirm (+ pay-off)

### Tool: `propose_payment` (replaces `record_payment`)

Mirrors `propose_meal`: it is the FINAL tool for recording a cash payment and it
**does not write the ledger** — it creates a pending draft the human confirms.

Schema:
- `from` (integer, optional; defaults to sender member id)
- `to` (integer, required)
- `amount` (integer, **optional**)
- `note` (string, optional)

Amount resolution (server-side, in the tool):
- If `amount` is given → use it verbatim (user stated a number).
- If `amount` is omitted → compute the **current settle-preview transfer from `from`→`to`**
  using the existing settlement logic (the natural "pay off what you owe" number).
  - If a transfer `from`→`to` exists → that amount.
  - If none exists (they owe `to` nothing) → return `{ok: false, reason: "already_settled"}`
    with a friendly message; **no draft created** (this also prevents the duplicate).
- The model is instructed never to invent an amount: omit it for "đã trả / trả đủ",
  supply it only when the user says an explicit number.

### Draft lifecycle (drafts.py)

New `kind="payment_draft"`, attachments:
`{type:"payment_draft", status:"pending"|"committed"|"cancelled", from_member_id, to_member_id, amount, note}`.

- `create_payment_draft(session, room_id, payload)` — mirror `create_draft`.
- `commit_payment_draft(session, draft_id, room_id, logged_by)` — validates pending +
  required fields, calls `ledger.record_payment(...)`, posts a committed bot card,
  flips `status` to committed. Mirror `commit_draft`.
- **Pending guard:** the "pending drafts block settle" logic in `settle_period` /
  `list_pending_drafts` must include BOTH `expense_draft` and `payment_draft` kinds.
  Generalize `list_pending_drafts` to filter `kind IN (expense_draft, payment_draft)`.

### Endpoint

`POST /api/rooms/{room_id}/drafts/{draft_id}/commit` branches by the draft's `kind`:
- `expense_draft` → `drafts.commit_draft` (unchanged)
- `payment_draft` → `drafts.commit_payment_draft`

`PATCH /drafts/{id}` cancel path already generic (status→cancelled); ensure it accepts
payment drafts too. No recommit for payments (out of scope).

### Frontend

New **payment confirm card** component, mirroring `expense-draft-card.tsx`:
- Pending: "X → Y  <amount>" with **Confirm** / **Cancel** buttons.
- Committed / cancelled: static confirmation state.
- `message-list.tsx` renders `kind==="payment_draft"` with the new card.
- Confirm → `POST …/commit`; Cancel → `PATCH …/{status:"cancelled"}` (existing client fns).

---

## Workstream 2 — Cursor skills infrastructure

Cursor SDK (`cursor-sdk` 0.1.9) has no skills/instructions API. Skills load from the
**workspace `.cursor/`** when `LocalAgentOptions.setting_sources` includes `"project"`.
Atlas's headless-bridge spikes established:
- `.cursor/rules/<name>.mdc` with `alwaysApply:true` → **hard rules, load every turn.**
- `.cursor/skills/<name>/SKILL.md` → **on-demand, description-triggered** (headless honors these).

### Mechanism (minimized Atlas pattern)

- Ship source files in-repo: `backend/app/agent_skills/rules/*.mdc` and
  `backend/app/agent_skills/skills/<name>/SKILL.md`.
- A **materializer** copies them into `<workspace>/.cursor/rules/` and
  `<workspace>/.cursor/skills/` before each turn, idempotently (skip rewrite when
  unchanged; force `alwaysApply:true` on rules). Pure filesystem, no DB, no tiers.
- Wire `LocalAgentOptions(setting_sources=["project"], cwd=workspace, custom_tools=…, store=…)`
  in [agent.py](../../../backend/app/agent.py).

### Content split

- **Rules (`.mdc`, always-on):** hard money invariants — never compute/retype an amount;
  every ledger change (meal, payment, settle) goes through a tool; a payment/meal is a
  proposal the human confirms.
- **Skills (`SKILL.md`, on-demand):** the procedures — *record a meal*, *record a
  payment* (one `propose_payment` per payer; omit amount to pay off; explicit amount
  only when stated), *settle/close a period*.
- The system prompt in [agent.py](../../../backend/app/agent.py) slims to identity +
  language + a pointer; procedural detail moves into rules/skills.

---

## Workstream 3 — Model → grok-4.5 (high, fast)

Live catalog cross-check (`Cursor.models.list`):
`grok-4.5` exposes `effort=['high']` (only value) and `fast=['false','true']`
(`fast=true` is the default variant).

- Set **`CURSOR_SDK_MODEL=grok-4.5-fast`** in the droplet `.env`.
  - Bare `grok-4.5` resolves to `fast=false` (`_non_fast_variant`); the `-fast` suffix
    selects `fast=true`. `effort=high` is intrinsic to every variant.
- No code change to `cursor_runner` needed.
- Verify one live turn actually runs (grok enabled for the plan + key valid).

---

## Workstream 4 — Wipe data

At deploy: `docker compose stop backend` → `rm /data/chiatienan.db*` → `docker compose up -d backend`
(schema rebuilt by `create_all`). Same procedure used earlier this session.

---

## Testing (TDD)

Backend (pytest):
- `propose_payment`: explicit amount → draft with that amount; omitted amount → draft
  with the settle-transfer amount; omitted amount with nothing owed → `already_settled`,
  no draft; `from==to` and unknown member → errors.
- `commit_payment_draft`: writes via `ledger.record_payment`, flips status, posts card;
  re-commit rejected.
- `list_pending_drafts` / settle guard includes payment drafts.
- Skills materializer: writes expected `.cursor/rules/*.mdc` (alwaysApply:true) and
  `.cursor/skills/*/SKILL.md`; idempotent second run.

Frontend (vitest):
- Payment card renders pending with Confirm/Cancel; committed/cancelled states.
- Confirm calls commit endpoint; cancel calls patch.
- `message-list` routes `payment_draft` to the card.

## Rollout

Single deploy: build → wipe data → set `CURSOR_SDK_MODEL=grok-4.5-fast` → restart →
verify a live payment propose→confirm turn and a settle.

## Open decisions (resolved)

- Pay-off amount = settle-preview transfer X→Y (not raw net balance). **Chosen.**
- Payment draft = its own `kind="payment_draft"` (not reusing `expense_draft`). **Chosen.**
