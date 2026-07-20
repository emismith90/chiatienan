# chiatienan — Teams Lunch-Splitting Bot — Design

**Date:** 2026-07-20
**Status:** Approved design — ready for implementation plan
**Repo:** `chiatienan` (this repo, currently empty)

---

## 1. Context & Goal

A group of ~6–7 colleagues eat lunch together; group size varies day to day (2–7), and
**anyone** can be the one who pays. We want to stop doing mental/manual math and settle up cleanly.

**Goal:** a bot that lives in a Microsoft Teams group chat. When someone pays, they `@mention`
the bot with a short natural-language message (and optionally a bill photo / screenshot). The bot
interprets it, records the meal in a running ledger, and echoes a breakdown. On demand
(e.g. *"who needs to pay this week"*) the bot nets everyone's balances over the requested period,
produces the minimal set of transfers, and returns a **VietQR** code per transfer so people can
pay by scanning.

**Non-goals (YAGNI):** no fixed weekly cadence/cron, no automatic debt clearing, no itemized
per-dish assignment, no multi-currency, no auth/RBAC beyond a single admin password.

---

## 2. Key Decisions (with rationale)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Channel: custom Teams bot** on the **Niteco work/school (M365) tenant**, in a new group | Personal/consumer Teams (`teams.live.com`) cannot host custom bots (no Bot Framework, no Graph chat access, no connectors). A work tenant supports custom bots properly. |
| D2 | **Engine: Cursor Agent SDK** (Python `cursor-sdk`) as an **agentic tool loop** | User preference. As of Jun 2026 the SDK supports custom tools (`local.customTools` / `CustomTool`) exposed via a built-in `custom-user-tools` MCP server, headless runs with `autoReview`. |
| D3 | **Money math is deterministic**, never done by the LLM | LLMs are unreliable at arithmetic; on money that destroys trust. The LLM *interprets*; **tools** (plain code) compute shares, netting, and rounding. |
| D4 | **Split model: equal by default + per-person overrides** | Matches how the group actually eats — usually even, occasionally "X didn't eat" or "Y's dish was pricier." |
| D5 | **Ledger is a pure, append-only record of spending** — nothing is ever "marked paid" or cleared | User choice. The wallet tracks how much each person paid vs. consumed. Outstanding debt is defined by the **period you query**, so the group just keeps a consistent habit (e.g. always ask "this week"). Corrections = **void + re-record**, never mutate. |
| D6 | **Settlement: netted, minimized transfers** | Fewest QR codes to scan. Computed on demand for the requested period. |
| D7 | **QR: `vietqr.app/img` image URL** (user-provided service) | No QR library needed; a tool builds an image URL from bank code + account + amount + note. |
| D8 | **Roster: admin sets everyone up** via a tiny `/admin` page | Bank account numbers are error-prone to type in chat; a form validates them once. |
| D9 | **Stack: pure-Python FastAPI backend**, no frontend framework | Teams is request/response; we don't need the scaffold's Next.js/AG-UI/SSE web chat. |
| D10 | **Deploy: DO App Platform + SQLite + Litestream → DO Spaces** | Easiest DO deploy (git-push, automatic HTTPS on `*.ondigitalocean.app` — solves the Teams public-HTTPS requirement with no domain/cert/VM). SQLite kept durable on ephemeral disk via Litestream replication to Spaces. |

**Reference material:** the Azure DevOps repo
`sample-cursor-sdk-with-image` is used **as reference only** for how to drive the Python
`cursor-sdk` — it is *not* forked or extended. See §8.

---

## 3. Architecture

One DigitalOcean **App Platform** service (single container, single instance), automatic HTTPS.
Pure-Python FastAPI backend. SQLite on the container's ephemeral disk, replicated to DO Spaces
via Litestream (restore on boot, replicate while running).

```
Teams group ──@mention + text/photo──▶ ┌───────────────────────────────┐
   ◀── breakdown / settlement + QR ──── │  FastAPI backend (Python)     │
                                        │                               │
Admin browser ──▶ /admin (tiny page) ─▶ │  teams.py   (botbuilder)      │→ Bot Connector (reply)
                                        │  agent.py   (Cursor SDK loop) │→ Cursor SDK (LLM + tools)
                                        │  tools.py   (CustomTools)     │→ ledger (SQLite)
                                        │  ledger.py / roster.py        │→ SQLite  ⇄ Litestream ⇄ DO Spaces
                                        │  qr.py                        │→ vietqr.app/img
                                        │  prompt.py / config.py        │
                                        │  images.py                    │
                                        └───────────────────────────────┘
```

### Modules (each independently testable)

| Module | Responsibility | Depends on |
|--------|----------------|-----------|
| `teams.py` | botbuilder adapter + `/api/messages`: verify inbound, parse `@mention` text + image attachments, **download** image (authenticated), run the agent to completion, reply (Adaptive Card for settlement) | botbuilder, Entra bot creds |
| `agent.py` | Cursor SDK orchestration: `launch_bridge → agents.create(AgentOptions) → send(text|images)`; run **to completion** (no streaming); collect final text + tool results | `cursor-sdk`, `tools`, `prompt`, `config` |
| `tools.py` | The `CustomTool` set — **all arithmetic lives here** | `ledger`, `roster`, `qr` |
| `ledger.py` | SQLite via a thin SQLAlchemy repository: append-only meals + shares, period balance queries, netting | SQLAlchemy, SQLite |
| `roster.py` | Member CRUD + name/alias/mention resolution | `ledger` DB |
| `qr.py` | Build `vietqr.app/img` URLs | `roster` |
| `prompt.py` | System prompt (Vietnamese-aware, lunch-splitting behavior + tool guidance) | — |
| `images.py` | Validate/sanitize + decode image attachments to base64 (pattern from reference scaffold) | — |
| `config.py` | Env settings | — |
| `admin` (route in `main.py`) | Password-protected `/admin` roster page (form + table) | `roster` |

**Design principle:** the LLM decides *when* to call tools; the tools own every number. The
extractor never sums or divides. Meals are immutable (append-only + `voided` flag).

---

## 4. Data Model (SQLite via SQLAlchemy)

- **`members`**
  `id, display_name, aliases (json[]), teams_user_id, bank_code, account_number,
  account_holder, active (bool), created_at`
  Admin-managed. `aliases` + `display_name` + `teams_user_id` feed `find_members`.

- **`meals`** *(append-only)*
  `id, occurred_on (date), payer_member_id, total_amount (int, VND), note, raw_input (text),
  source (teams|admin), logged_by (teams_user_id), voided (bool, default false), created_at`

- **`meal_shares`**
  `id, meal_id (fk), member_id (fk), share_amount (int, VND)`
  One row per participant = that person's consumption for the meal. Sum of a meal's shares
  equals its `total_amount` exactly.

**Balances are derived, never stored** (pure ledger). For a period `[from, to]`, ignoring `voided`
meals, per member:
- `paid = Σ meals.total_amount where payer = member`
- `consumed = Σ meal_shares.share_amount where member = member`
- `balance = paid − consumed` (positive = is owed; negative = owes)

Netting reduces balances to a minimal transfer list (see `net_transfers`).

---

## 5. Tools (the `CustomTool` set)

Each follows the reference shape `CustomTool(execute=fn, description=..., input_schema=...)`,
`execute(args, ctx) -> dict`.

| Tool | Behavior (deterministic) |
|------|--------------------------|
| `find_members(names[], mentions[])` | Resolve free-text names / `@mentions` → member ids; return matched + **unresolved** so the agent can ask a clarifying question |
| `record_meal({payer, participants[], total, adjustments[], occurred_on?, note?})` | Compute shares, write `meals` + `meal_shares`, return breakdown + `meal_id`. **All arithmetic here.** |
| `void_meal(meal_id)` | Soft-delete (`voided = true`) for corrections |
| `get_period_balances({from, to})` | Per-member `paid / consumed / balance` over the period |
| `net_transfers(balances)` | Minimized who-pays-whom list `[{from, to, amount}]` |
| `make_qr({to_member, amount, note})` | Build the `vietqr.app/img` URL for a transfer |

### Split math (inside `record_meal`)

Given `total`, participant set `P`, and per-person `adjustments` (signed VND deltas):
- `base = (total − Σ adjustments) / |P|` (integer VND)
- each participant's share = `base + their adjustment`
- the integer-division **remainder is assigned deterministically to the payer**, so shares always
  sum to `total` exactly.

Override semantics: *"An didn't eat"* → An not in `participants`; *"Bình +50k"* → Bình adjustment
`+50000`. Equal split is the case where `adjustments` is empty.

---

## 6. Key Flows

### 6.1 Log a meal
1. User: `@chiatienan 840k cả nhóm trừ An, Bình +50k` (± bill photo).
2. `teams.py` downloads any image (authenticated), builds the agent input (text + base64 image).
3. Agent: `find_members` → `record_meal`.
4. Bot replies with the breakdown + meal id, e.g.
   *"Tổng 840k • An —, Bình 260k, 5 người ×116k • đã ghi #42 ✅"*.
5. **Correction:** *"xoá 42"* or *"sửa #42 ..."* → `void_meal(42)` (+ re-record if fixing).
   No cross-turn "pending confirmation" state to maintain.

### 6.2 Settle / summary
1. User: `@chiatienan ai trả tuần này`.
2. Agent resolves the period → `get_period_balances` → `net_transfers` → `make_qr` per debtor.
3. Bot posts an **Adaptive Card**: a who-owes-whom table + one VietQR image per transfer.
   (Adaptive Cards render tables/images reliably; plain Teams markdown does not.)

### 6.3 Roster admin
- Admin opens `/admin` (password-protected), adds/edits members: display name, aliases,
  Teams user, bank code, account number, account holder.

---

## 7. Teams Integration Specifics

- **Bot receives group messages only when `@mentioned`** (standard Teams group-chat behavior) —
  matches the "tag the bot" UX.
- **Inbound auth:** botbuilder validates the Bot Connector JWT.
- **Images:** inline images arrive as attachments; the bot downloads content using its bearer
  token, then `images.py` sanitizes → base64 for a multimodal `UserMessage`.
- **Replies:** normal turn replies (no proactive messaging needed) — text/markdown for the
  breakdown echo, Adaptive Card for settlements.
- **Vision:** default model `composer-2.5` (vision-capable). Text-only models would strip images.

---

## 8. Cursor SDK usage (patterns replicated from the reference scaffold)

The `sample-cursor-sdk-with-image` repo is reference only. Reuse these **known-good patterns**:
- Orchestration: `AsyncClient.launch_bridge → agents.create(AgentOptions(model, api_key, local,
  mcp_servers)) → agent.send(message, SendOptions(...))`.
- **Model resolution:** bare parameterized ids (e.g. `composer-2.5`) fail with
  `RUN_LIFECYCLE_STATUS_ERROR`; must be sent as a resolved `ModelSelection` (the scaffold's
  `resolve_model_selection`). Replicate this.
- `CustomTool(execute, description, input_schema)` registration; the `name=='mcp'` result unwrap.
- **Multimodal send:** `UserMessage(text=..., images=[SDKImage.data_image(data, mime)])`.
- Turn-storm cap (`CURSOR_AGENT_MAX_TOOLS` / `..._MAX_SECONDS`) + interrupt close-out.
- **Difference from scaffold:** we run **to completion** and assemble one final message for Teams,
  rather than streaming AG-UI/SSE to a browser.

**Key requirement:** `CURSOR_API_KEY` must be a **user or service-account** key (Team Admin keys
do not work with the SDK).

---

## 9. Deployment (DO App Platform + SQLite + Litestream)

- **App Platform** service built from this repo's Dockerfile; **automatic HTTPS** on a
  `*.ondigitalocean.app` subdomain → used as the Teams messaging endpoint
  (`https://<app>.ondigitalocean.app/api/messages`). No domain/cert/VM to manage.
- **SQLite durability via Litestream:** container entrypoint runs `litestream restore` (pull latest
  DB from DO Spaces on boot), then `litestream replicate -exec "<app start>"` so Litestream
  supervises the app and streams the WAL to Spaces (~1s interval).
- **Constraints (acceptable at this scale):**
  - **Single instance** (Litestream = one writer); App Platform component fixed at 1 instance,
    no horizontal scaling.
  - **~1s durability window** — an ungraceful kill can lose the last ~1s of writes.
- **`.do/app.yaml`** committed for reproducible deploys.
- **Cost:** ~$5 app + ~$5 Spaces ≈ **$10/mo**.

### Config / secrets (App Platform env vars)
| Var | Purpose |
|-----|---------|
| `CURSOR_API_KEY` | Cursor SDK (user/service-account key) |
| `CURSOR_SDK_MODEL` | default `composer-2.5` |
| `MICROSOFT_APP_ID` / `MICROSOFT_APP_PASSWORD` | Entra bot registration |
| `SPACES_KEY` / `SPACES_SECRET` / `SPACES_BUCKET` / `SPACES_ENDPOINT` | Litestream → Spaces |
| `DATABASE_URL` | `sqlite:///./data/data.db` (local + prod; prod file is Litestream-backed) |
| `ADMIN_PASSWORD` | `/admin` page auth |

---

## 10. Testing

- **Unit (pytest, pure, high-value):**
  - ledger math — shares, remainder-assigned-to-payer sums exactly to total, netting/minimization;
  - each tool function;
  - image sanitize (port the scaffold's tests);
  - Teams activity parsing (mocked activities);
  - agent smoke test with a mocked Cursor SDK.
- **E2e (manual):** deploy to App Platform, sideload the app into a test Teams group, run a
  log + a settle; verify QR images render in the Adaptive Card and the ledger persists across a
  redeploy (Litestream restore).

---

## 11. Prerequisites / IT

- Teams admin allows **custom app upload / sideloading** on the Niteco tenant (or approves the app).
- An **Azure Bot resource** + **Entra app registration** with the messaging endpoint set to
  `https://<app>.ondigitalocean.app/api/messages`.
- A **DO Spaces** bucket + access keys for Litestream.
- Sample logging messages / bill photos (user to provide) → used to tune the extraction prompt and
  seed test fixtures.

---

## 12. Out of Scope (documented follow-ups)

- Auth/RBAC beyond the single admin password.
- Automatic debt clearing / "mark as paid" (pure ledger by design — D5).
- Itemized per-dish splitting; multi-currency; e-wallets (MoMo/ZaloPay) — personal bank VietQR only.
- Fixed weekly cadence/cron (settlement is on-demand).
- Horizontal scaling (single-instance by Litestream design).
- The scaffold's Next.js web chat / AG-UI streaming (reference only).
