# chiatienan — Teams Lunch-Splitting Bot — Design

**Date:** 2026-07-20
**Status:** ⚠️ SUPERSEDED by `2026-07-20-chiatienan-pwa-design.md` — the Teams channel was
abandoned (IT would not approve a personal custom Teams app / sideloading). The deterministic
ledger + Cursor-SDK agent core described here is reused by the PWA design; only the Teams gateway
was dropped. Kept for history.
**Repo:** `chiatienan` (this repo, currently empty)

---

## 1. Context & Goal

A group of ~6–7 colleagues eat lunch together; group size varies day to day (2–7), and
**anyone** can be the one who pays. We want to stop doing manual math and settle up cleanly.

**Goal:** a bot in a Microsoft Teams group chat. When someone pays, they `@mention` the bot with a
short natural-language message (and optionally a bill photo / screenshot). The bot interprets it,
records the meal in a running ledger, and echoes a breakdown. On demand (e.g. *"who needs to pay
this week"*) the bot nets everyone's balances over the requested period, produces the minimal set
of transfers, and returns a **VietQR** code per transfer so people can pay by scanning.

**Non-goals (YAGNI):** no fixed weekly cadence/cron, no per-dish itemization, no multi-currency,
no auth/RBAC beyond a single admin password.

---

## 2. Key Decisions (with rationale)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Channel: custom Teams bot** on the **Niteco work/school (M365) tenant**, in a new group | Personal/consumer Teams (`teams.live.com`) cannot host custom bots. A work tenant supports them properly. |
| D2 | **Engine: Cursor Agent SDK** (Python `cursor-sdk`) as an **agentic tool loop** | User preference. Supports custom tools (`CustomTool`) + headless runs. |
| D3 | **Money math is deterministic** — the LLM never computes *or transcribes* a number that ends up in a QR | LLMs are unreliable at both arithmetic and transcription. Rule: **numbers may flow user→LLM→tool once (as input), but never tool→LLM→tool.** |
| D4 | **Split model: equal by default + per-person overrides** | Matches how the group eats — usually even, occasionally "X didn't eat" / "Y's dish was pricier." |
| D5 | **Meals are an immutable, append-only ledger; settlements are a separate append-only event log** | The meal ledger is never mutated (corrections = void + re-record). A **`settlements` table** records committed settle events so the default query is "since the last settlement" — fixing double-collection/orphaned-meal problems without ever mutating a meal. |
| D6 | **Settlement: netted, minimized transfers** (greedy max-debtor → max-creditor) | Fewest QR codes. Truly-minimal is NP-hard; greedy is good enough and predictable. |
| D7 | **QR: `vietqr.app/img` image URL** (user-provided service) | No QR library; a server-side function builds the image URL. |
| D8 | **Roster: admin sets everyone up** via a tiny `/admin` page; Teams IDs captured automatically | Bank numbers are error-prone to type in chat; a form validates once. Nobody knows their own `29:…` Teams id, so the bot captures it on first mention. |
| D9 | **Stack: pure-Python FastAPI backend**, no frontend framework | Teams is request/response; no web chat / SSE needed. |
| D10 | **Deploy: DigitalOcean droplet + Docker Compose + Caddy (auto-TLS) + SQLite on a volume** | Chosen over App Platform to avoid the unverified `cursor-sdk-bridge`-under-gVisor risk and the Litestream rolling-deploy data-loss window. A plain container gives the bridge a normal environment and a persistent disk gives SQLite a real single-writer file. Requires a domain for HTTPS (Teams needs a valid public HTTPS endpoint). |

**Reference material:** the Azure DevOps repo `sample-cursor-sdk-with-image` and the Niteco **Atlas**
agent codebase are used **as reference only** for how to drive the Python `cursor-sdk` — not forked.
See §8.

---

## 3. Architecture

One DigitalOcean **droplet** (≥1 GB RAM — see §9), Docker Compose: a **Caddy** reverse proxy
(automatic Let's Encrypt TLS) in front of the **FastAPI** backend. SQLite lives on a persistent
volume. Pure-Python, no frontend framework.

```
Teams group ──@mention + text/photo──▶ Caddy (TLS) ─▶ ┌──────────────────────────────┐
   ◀── breakdown / settlement + QR ─── (proactive) ── │  FastAPI backend (Python)    │
                                                       │                              │
Admin browser ──▶ /admin (password) ──▶ Caddy ──────▶ │  teams.py  (botbuilder)      │→ Bot Connector
                                                       │  worker    (async turn queue)│→ Cursor SDK (LLM+tools)
                                                       │  agent.py  (Cursor SDK)      │→ ledger (SQLite/WAL)
                                                       │  tools.py  (CustomTools)     │→ vietqr.app/img
                                                       │  ledger/roster/qr/prompt     │
                                                       │  images.py / config.py       │
                                                       └──────────────────────────────┘
                                                              SQLite file on droplet volume
```

### Turn model (fixes the Bot Connector timeout / duplicate-write problem)

The Bot Connector delivers an inbound activity with a **~15 s timeout and retries on timeout**. A
full agent run (bridge launch + vision turn + tool calls) exceeds that. Therefore:

1. `/api/messages` **acknowledges 200 immediately** and enqueues the turn (in-process asyncio
   queue, **concurrency = 1** — a semaphore that also serializes SQLite writes).
2. A worker runs the agent to completion, then **replies proactively** via the saved
   `ConversationReference` (`adapter.continue_conversation`).
3. **Idempotency:** each processed `activity.id` is recorded; a re-delivered activity is dropped
   before it can write a second meal.

### Modules

| Module | Responsibility | Depends on |
|--------|----------------|-----------|
| `teams.py` | botbuilder adapter + `/api/messages`: verify inbound (single-tenant auth), **immediate 200 + enqueue**, strip `<at>` mention, parse **inline** image attachments, capture `from.id`/`aadObjectId`, proactive reply (Adaptive Card for settlement) | botbuilder, Entra bot creds |
| `worker` | Async turn queue (concurrency 1), activity-id dedup, per-turn error → fallback reply | `agent`, `ledger` |
| `agent.py` | Cursor SDK: `launch_bridge` (with launch-retry) → `agents.create` → `send(text\|images)`; run **to completion**; collect final text + tool results | `cursor-sdk`, `tools`, `prompt`, `config` |
| `tools.py` | The `CustomTool` set — **all arithmetic + all number-carrying here** | `ledger`, `roster`, `qr` |
| `ledger.py` | SQLite (WAL) via SQLAlchemy repo: append-only meals+shares, settlements log, balance queries, netting; single insert transaction for meal+shares | SQLAlchemy, SQLite |
| `roster.py` | Member CRUD; name/alias/mention + `all_active` resolution | ledger DB |
| `qr.py` | Build `vietqr.app/img` URLs (URL-encode Vietnamese `des`) | roster |
| `prompt.py` | Vietnamese-aware system prompt + tool guidance | — |
| `images.py` | Validate/sanitize/decode **inline** images → base64 (pattern from reference) | — |
| `config.py` | Env settings | — |
| `main.py` | Mounts routes: `/api/messages`, `/admin` (password), **`/health`** | roster |

**Design principle:** the LLM decides *when* to call tools; tools own every number. Numbers never
round-trip tool→LLM→tool (D3).

---

## 4. Data Model (SQLite/WAL via SQLAlchemy)

All timestamps/dates computed in **Asia/Ho_Chi_Minh** (ICT, UTC+7). "Week" = **Monday–Sunday**.
`DATABASE_URL` uses an **absolute** path (must match Caddy/volume + any tooling byte-for-byte).
SQLite runs with `journal_mode=WAL`, `busy_timeout`, and a single application writer (worker
concurrency 1).

- **`members`**
  `id, display_name, aliases (json[]), teams_user_id, aad_object_id, bank_code, account_number,
  account_holder, active (bool), created_at`
  Admin-managed; `teams_user_id`/`aad_object_id` captured from first `@mention` and linked in `/admin`.

- **`meals`** *(append-only, immutable)*
  `id, occurred_on (date, ICT), payer_member_id, total_amount (int VND), note, raw_input (text),
  source (teams|admin), logged_by (teams_user_id), voided (bool default 0), voided_by, voided_at,
  created_at`

- **`meal_shares`**
  `id, meal_id (fk), member_id (fk), share_amount (int VND)`
  One row per participant = that person's consumption. `Σ shares == meals.total_amount` exactly.

- **`settlements`** *(append-only event log)*
  `id, period_from (date), period_to (date), created_at, requested_by (teams_user_id),
  transfers (json snapshot)`
  Records a **committed** settle. The default settlement window is `(last_settlement.period_to, now]`.

**Balances derived, never stored.** For a window, excluding `voided` meals, per member:
`paid = Σ meals.total_amount where payer=member`, `consumed = Σ meal_shares.share_amount`,
`balance = paid − consumed`.

---

## 5. Tools (the LLM-facing `CustomTool` set)

Shape: `CustomTool(execute=fn, description=..., input_schema=...)`, `execute(args, ctx) -> dict`.

| Tool | Behavior (deterministic) |
|------|--------------------------|
| `find_members({names[], mentions[], all_active?})` | Resolve free-text names / `@mentions` → member ids. `all_active:true` returns every active member (for "cả nhóm"), so the LLM never enumerates the roster from memory. Returns matched + **unresolved**. |
| `record_meal({payer, participants[], total, adjustments[], occurred_on?, note?})` | Validate, compute shares, write `meals`+`meal_shares` in **one transaction**, return breakdown + `meal_id`. All arithmetic here. |
| `void_meal({meal_id, by})` | Soft-delete (`voided=1`, record `voided_by/at`) for corrections |
| `resolve_period({keyword})` | Deterministic ICT date math → `{from,to}`. Supports `"since_last"` (default), `"this_week"`, `"last_week"`, explicit dates. Week = Mon–Sun. |
| `get_period_balances({from, to})` | Per-member `paid/consumed/balance` — **display-only** (e.g. "how much did I spend this month"); its numbers are never fed into another tool |
| `settle_period({from, to, commit})` | **Composite, server-side end-to-end:** compute balances → net transfers → build every VietQR URL → return the finished settlement payload (table rows + QR image URLs). If `commit:true`, append a `settlements` row. The LLM only chose the period and renders the card. |

**Internal (not LLM tools):** `net_transfers(balances)` (greedy), `make_qr(to_member, amount, note)`
— pure functions called *inside* `settle_period`, so no amount ever passes through the LLM.

### Split math (inside `record_meal`) — with edge cases

Given `total`, participant set `P`, per-person signed `adjustments` (VND):
- `base = (total − Σ adjustments) // |P|`; each share = `base + their adjustment`.
- **Remainder** (`total − Σ shares`) assigned deterministically to the **payer if the payer is a
  participant, else to the first participant** — so `Σ shares == total` always.
- **Payer not eating** ("An trả nhưng không ăn"): payer simply isn't in `P` and gets no share row
  (still recorded as `payer_member_id`; they paid, consumed 0).
- **Validation (reject with a clarifying-question result):** `|P| ≥ 1`; every resulting share `≥ 0`;
  `total > 0`; `Σ adjustments ≤ total`. Never write a negative or nonsensical share.

---

## 6. Key Flows

### 6.1 Log a meal
1. User: `@chiatienan 840k cả nhóm trừ An, Bình +50k` (± bill photo).
2. `/api/messages` → 200 + enqueue. Worker: strip mention, decode inline image.
3. Agent: `find_members(all_active, minus An)` → `record_meal`.
4. Proactive reply: *"Tổng 840k • An —, Bình 260k, 5 người ×116k • đã ghi #42 ✅"*.
5. **Correction:** *"xoá 42"* / *"sửa #42 …"* → `void_meal` (+ re-record if fixing). No cross-turn
   pending-confirmation state. `record_meal` is the **last** tool in a log turn, so a failed run
   never half-writes.

### 6.2 Settle / summary
1. User: `@chiatienan ai trả tuần này` (or nothing → "since_last").
2. Agent: `resolve_period` → `settle_period({from,to, commit:false})` (preview).
3. Proactive **Adaptive Card**: who-owes-whom via `ColumnSet`/`FactSet` (not the 1.5 `Table`
   element — patchy Teams support) + one VietQR image per transfer; card text hints "scan from your
   banking app / import from gallery if it's on this phone."
4. To lock it in: `@chiatienan chốt tuần này` → `settle_period(commit:true)` appends a
   `settlements` row so the next "since_last" starts fresh. **This is the only way a period is
   considered settled** — prevents double-collection and orphaned meals.

### 6.3 Roster admin
- Admin opens `/admin` (password), edits members (name, aliases, bank details) and links the
  Teams identities the bot has captured from first mentions.

---

## 7. Teams Integration Specifics

- Bot receives group messages only when `@mentioned` — matches the "tag the bot" UX. The raw
  `text` contains `<at>chiatienan</at>`; **strip the mention entity** before building agent input.
- **Inbound auth:** botbuilder with **single-tenant** config — `MicrosoftAppType=SingleTenant` +
  `MicrosoftAppTenantId` (Niteco IT default), else inbound/outbound calls 401.
- **Replies are proactive** (async turn model, §3) via the saved `ConversationReference`.
- **Images: inline only.** Pasted inline images download with the bot bearer token. A bill photo
  attached **as a file** goes to SharePoint/OneDrive and is **not** retrievable with the bot token
  (needs Graph + consent) — out of scope; when the bot sees a file attachment it says "paste the
  photo inline instead."
- **Vision:** default model `composer-2.5` (vision-capable).
- **Deliverable:** a **Teams app package** — `manifest.json` (`scopes: ["groupChat"]`) + color/outline
  icons — required to sideload; listed in §11.

---

## 8. Cursor SDK usage (patterns replicated from reference; not forked)

- Orchestration: `AsyncClient.launch_bridge → agents.create(AgentOptions(model, api_key, local,
  mcp_servers)) → agent.send(message, SendOptions(...))`.
- **Launch is transiently flaky** (the Atlas backend needs a `_launch_bridge_resilient` retry
  because the bridge sometimes "exited before discovery") — replicate a **launch-retry**.
- **Bridge is a subprocess** needing a writable workspace cwd + sqlite store dir — provided by the
  droplet container (a normal, non-gVisor environment); `SandboxOptions` **off** (observed rejected
  in plain Linux containers).
- **Bridge lifecycle decision:** per-turn `launch_bridge` (simple; adds seconds + flake surface per
  message) vs a warm persistent bridge (lower latency; must handle restarts). Default: **per-turn**
  at this volume; revisit if latency annoys.
- **Model resolution:** send `composer-2.5` as a resolved `ModelSelection` (bare id → `RUN_LIFECYCLE_STATUS_ERROR`).
- `CustomTool(execute, description, input_schema)`; the `name=='mcp'` result unwrap.
- **Multimodal:** `UserMessage(text=…, images=[SDKImage.data_image(data, mime)])`.
- Turn-storm cap (`CURSOR_AGENT_MAX_TOOLS` / `…_MAX_SECONDS`) + interrupt close-out.
- **`CURSOR_API_KEY` must be a user/service-account key** (Team Admin keys don't work).
- Difference from reference: run **to completion**, assemble one final Teams message (no AG-UI/SSE).

---

## 9. Deployment (DigitalOcean droplet)

- **Droplet ≥ 1 GB RAM** (Python + FastAPI + SQLAlchemy + botbuilder + a bridge subprocess + a
  vision turn; the 512 MB tier risks OOM). Docker Compose: `caddy` + `backend`.
- **Caddy** terminates TLS with automatic Let's Encrypt for the droplet's **domain** →
  Teams messaging endpoint `https://<domain>/api/messages`.
- **SQLite** on a mounted **volume** (persistent). WAL mode; absolute `DATABASE_URL` path on the
  volume. Nightly `cron` copy of the DB file to DO Spaces (or `sqlite3 .backup`) for backup.
- **Concurrency = 1** worker (serializes writes; bounds memory to one bridge/turn at a time).
- **Cost:** ~$6–12/mo droplet (size for RAM) + optional Spaces backup.

### Config / secrets (env)
| Var | Purpose |
|-----|---------|
| `CURSOR_API_KEY` | Cursor SDK (user/service-account key) |
| `CURSOR_SDK_MODEL` | default `composer-2.5` |
| `MICROSOFT_APP_ID` / `MICROSOFT_APP_PASSWORD` | Entra bot registration |
| `MICROSOFT_APP_TYPE` / `MICROSOFT_APP_TENANT_ID` | `SingleTenant` + Niteco tenant id |
| `DATABASE_URL` | `sqlite:////data/chiatienan.db` (absolute, on the volume) |
| `TZ` | `Asia/Ho_Chi_Minh` |
| `ADMIN_PASSWORD` | `/admin` auth |
| `CADDY_DOMAIN` | droplet domain for TLS |

---

## 10. Testing

- **Unit (pytest, pure, high-value):**
  - ledger math — shares sum-exactly incl. **payer-not-participant, negative/overshoot adjustment
    rejection, remainder sign**; greedy netting; balance windows incl. "since_last".
  - each tool; `resolve_period` boundaries (Mon–Sun, ICT edges, evening logs).
  - **idempotency** — duplicate `activity.id` delivered twice writes one meal (B1 regression).
  - image sanitize (port reference tests); mention stripping; Teams activity parsing (mocked).
  - agent smoke with a mocked Cursor SDK.
- **E2e (manual):** deploy to the droplet, sideload into a test Teams group; run a log
  (inline photo) + a preview + a `chốt`; verify QR images render in the card and the ledger
  persists across a container restart.

---

## 11. Prerequisites / IT

- **Domain + DNS** A-record → droplet (Caddy needs it for TLS; Teams needs valid HTTPS).
- Teams admin allows **custom app sideloading** (or approves the app).
- **Azure Bot resource** + **single-tenant Entra app registration**; messaging endpoint
  `https://<domain>/api/messages`.
- **Teams app package** (`manifest.json` `groupChat` scope + icons).
- Sample logging messages / bill photos (user to provide) → tune the extraction prompt + seed
  test fixtures.

---

## 12. Out of Scope (documented follow-ups)

- Auth/RBAC beyond the single admin password.
- Per-dish itemization; multi-currency; e-wallets (MoMo/ZaloPay) — personal-bank VietQR only.
- **File-attachment** bill photos (SharePoint/Graph) — inline images only.
- Fixed weekly cadence/cron (settlement is on-demand; `chốt` commits it).
- Warm persistent bridge / horizontal scaling (per-turn bridge, single worker for now).
- The reference scaffold's Next.js web chat / AG-UI streaming.
