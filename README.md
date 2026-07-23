# chiatienan — lunch-splitting PWA

[![CI](https://github.com/emismith90/chiatienan/actions/workflows/ci.yml/badge.svg)](https://github.com/emismith90/chiatienan/actions/workflows/ci.yml)

A self-hosted chat app (installable PWA) for a group of ~6–7 colleagues who eat
lunch together. Everyone joins a shared **room** and chats in Vietnamese. When
someone pays, they `@mention` the bot with a short natural-language message
(optionally a bill photo). The bot interprets it with an LLM and posts an
**editable expense-draft card**; a human confirms it, and only then is the meal
written to an append-only ledger. On demand (*"@bot ai trả tuần này"*) it nets
everyone's balances over the requested period, produces the minimal set of
transfers, and returns a **VietQR** code per transfer so people pay by scanning.

The LLM decides *when* to call tools; **the tools own every number**. A number
may flow user → LLM → tool **once** (as input), but never tool → LLM → tool — so
no amount that ends up in a QR is ever computed or transcribed by the model.
Meals are never written by the model directly: it can only *propose*, and a
person edits/commits the draft.

> Design: [`docs/superpowers/specs/2026-07-20-chiatienan-pwa-design.md`](docs/superpowers/specs/2026-07-20-chiatienan-pwa-design.md)
> and the [chat-UX overhaul](docs/superpowers/specs/2026-07-20-chat-ux-overhaul-design.md).
> (The original [Teams-bot design](docs/superpowers/specs/2026-07-20-chiatienan-teams-lunch-bot-design.md)
> predates the PWA pivot and is kept for history only.)

## Architecture

```
Phone/browser (installable PWA)
   │  Next.js 16 (React 19) — room chat, join/claim, profile
   ▼
Caddy (auto-TLS)
   │  /api/*, /internal/*  ──▶  FastAPI backend (single uvicorn process)
   └  everything else      ──▶  Next.js (standalone)
                                   │
   room chat ── @bot ─────────────┤  chat.py     @bot detect + dispatch (serialized)
   live updates ◀── SSE ──────────┤  realtime.py in-process RoomHub pub/sub
                                   │  agent.py    Cursor SDK (LLM + tools), run-to-completion
                                   │  tools.py    CustomTools (all arithmetic + QR)
                                   │  drafts.py   editable expense-draft lifecycle
                                   │  ledger/roster/accounts/qr/money/periods
                                   └  SQLite (WAL) on a mounted volume
```

- **Auth:** an admin (holding `ADMIN_PASSWORD`) creates a room via `POST /api/rooms`,
  which mints an **invite token**. Anyone with the invite link joins with a
  nickname + PIN and gets a bearer-token session. Accounts the bot/admin add
  ahead of time are *unclaimed* (no PIN) and get claimed on first sign-in.
- **Chat + realtime:** human messages are persisted; a message that mentions
  `@bot` fires a **background agent turn**. Clients subscribe to
  `GET /api/rooms/{id}/stream` (SSE): it replays missed messages (`?since=`),
  streams live `agent.*` progress and new messages, heartbeats every 25 s, and
  drops slow clients so they reconnect.
- **Money safety (design D3):** meal turns end as a pending `expense_draft`
  card the user edits/commits — the ledger is never written from LLM prose.
  Settlement/meal bodies are rendered server-side from the tool-result dict, so
  the visible text can never disagree with the QR amounts.
- **Single writer:** the agent runs are serialized by an in-process
  `asyncio.Lock`, and SQLite runs in WAL mode — correct **only** with a single
  backend process (see the Dockerfile note; do not add `--workers` or replicas).

### Backend modules (`backend/app/`)

| Module | Responsibility |
|--------|----------------|
| `main.py` | FastAPI app + all routes (rooms, join/identify, `/api/me`, messages, drafts, SSE stream, `/health`, guarded `/internal/bridge-smoke`) |
| `config.py` | Frozen env settings |
| `db.py` | SQLite engine (WAL, `busy_timeout`, FKs) + session scope |
| `models.py` | SQLAlchemy models: rooms, members, sessions, room_messages, meals, meal_shares, settlements |
| `money.py` | `split_with_guests` (equal base + signed overrides + guest heads, remainder rule) + `net_transfers` (greedy) |
| `periods.py` / `clock.py` | ICT period math (`since_last`, `this_week`…; week = Mon–Sun) + ICT time helpers |
| `ledger.py` | Append-only meals/shares/settlements, void, derived balances |
| `roster.py` | Room-scoped member listing + name/alias/mention resolution |
| `accounts.py` | Join / identify (claim unclaimed) / profile, unclaimed placeholders, soft-delete + restore, device sessions |
| `auth.py` | Bearer-session (`require_session`) + admin-password (`require_admin`) guards |
| `rooms.py` | Room create + lookup by invite token / id |
| `chat.py` | Persist/list messages, `@bot` detection, agent dispatch (serialized), deterministic bot-reply rendering |
| `drafts.py` | Expense-draft lifecycle: persist, edit, commit, supersede, cancel |
| `tools.py` | The LLM-facing `CustomTool` set (find/propose/void/period/balances/settle + member CRUD) |
| `prompt.py` | Vietnamese-aware system prompt + tool guidance |
| `images.py` | Inline-image sanitize (vision) |
| `qr.py` | VietQR image URL builder (pure, no network) |
| `agent.py` / `cursor_runner.py` | Cursor SDK wiring + per-turn bridge launch (with retry), run-to-completion |
| `agui.py` | Cursor run-message → `agent.*` SSE event translator (live-only) |
| `realtime.py` | In-process `RoomHub` pub/sub feeding the SSE streams |
| `bridge_smoke.py` | Guarded Cursor-bridge validation (B3) |

### Frontend (`frontend/src/`, Next.js 16 / React 19)

- `app/page.tsx` — the room view (or an "open an invite link" prompt when signed out).
- `app/join/[token]/page.tsx` — join screen: pick an unclaimed name or create an
  account, set a PIN, or identify with nickname + PIN.
- `components/chat/` — `room-view`, `message-list`, `composer` (with paste-to-attach
  + `mention-dropdown`), `bot-message`, `expense-draft-card`, `balance-table`,
  `agent-timeline` (live tool progress), `zoomable-image`.
- `hooks/use-room.ts` — SSE subscription + optimistic-send merge/dedupe by id.
- `lib/` — `api`, `sse`, `session`, `format`, `theme`, `sw-register`, `utils`.
- **PWA:** `public/manifest.webmanifest` + `public/sw.js` (registered via
  `sw-register`) make it installable to a home screen.

## Usage (in the room chat, mention `@bot`)

- Log a meal: `@bot 840k cả nhóm trừ An, Bình +50k` (± a pasted bill photo) →
  posts an **editable draft card**; tap to adjust payer/participants/total, then **Confirm**.
- Payer didn't eat: `@bot An trả 200k nhưng không ăn, chia Bình và Cường`
- Correct a recorded meal: `@bot xoá 42`
- Preview who-owes-whom: `@bot ai trả tuần này`
- Lock it in (the only thing that closes a period): `@bot chốt tuần này`
- Display-only spend: `@bot tháng này tôi tiêu bao nhiêu`
- Manage members: `@bot thêm thành viên Dũng`, `@bot đổi tên An thành Anh`,
  `@bot xoá thành viên Cường` (soft-delete), `@bot khôi phục Cường`.
- Reset the bot's conversation memory: `/clear` — summarizes the recent chat into
  the room's long-term memory and starts a fresh context window (the chat history
  stays visible; the ledger is untouched).

Bill photos must be **pasted inline** (the composer supports paste-to-attach).
Edit your own display name and bank details on the profile screen so the
settlement QR can pay you.

## Local development

Backend (tests need no network / SDK):

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest -q
```

Run the full stack locally — the Next.js dev server rewrites `/api/*` and
`/internal/*` to the backend (mirroring Caddy), so the browser only talks to `:3000`:

```bash
# terminal 1 — backend (CURSOR_API_KEY only needed for actual @bot turns)
cd backend && cp ../.env.example ../.env   # then edit ../.env
CURSOR_API_KEY=… ADMIN_PASSWORD=… uvicorn app.main:app --reload

# terminal 2 — frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
npm test                                     # vitest unit suite
```

Then create a room and open its invite link:

```bash
curl -X POST http://localhost:3000/api/rooms \
  -H "X-Admin-Password: $ADMIN_PASSWORD" -H "content-type: application/json" \
  -d '{"name":"Lunch"}'
# → open /join/<invite_token> in the browser
```

(There's also a `run-chiatienan` skill that launches both together.)

## Configuration

Copy `.env.example` → `.env` and fill it in. Key vars:

| Var | Purpose |
|-----|---------|
| `CURSOR_API_KEY` | Cursor SDK (a **user/service-account** key — not a Team Admin key) |
| `CURSOR_SDK_MODEL` | default `composer-2.5` (vision-capable) |
| `CURSOR_AGENT_MAX_TOOLS` / `CURSOR_AGENT_MAX_SECONDS` | per-turn runaway caps (40 / 120 s) |
| `BOT_HANDLE` | the `@`-handle the bot answers to in chat (default `bot`) |
| `DATABASE_URL` | `sqlite:////data/chiatienan.db` (absolute, on the volume) |
| `TZ` | `Asia/Ho_Chi_Minh` |
| `ADMIN_PASSWORD` | guards `POST /api/rooms` and `/internal/bridge-smoke` |
| `QR_BASE_URL` / `QR_TEMPLATE` | VietQR image endpoint + template |
| `CADDY_DOMAIN` | droplet domain for TLS (and the invite-link base) |

## Deploy (DigitalOcean droplet)

Full runbook: [`deploy/README.md`](deploy/README.md). In short:

1. Provision a droplet (**≥ 2 GB RAM** recommended; add swap on 1 GB) with Docker
   + Compose, and point an A-record at it (`CADDY_DOMAIN`).
2. Clone the repo, `cp .env.example .env`, and fill in `.env`.
3. Bring up all three services (Caddy + backend + frontend) from the repo root —
   Caddy fetches TLS automatically:

   ```bash
   docker compose up -d --build
   ```

4. Validate the Cursor SDK bridge runs in-container (B3):
   `curl -X POST https://<CADDY_DOMAIN>/internal/bridge-smoke -H "X-Admin-Password: <ADMIN_PASSWORD>"`.
5. Create the first room and share its invite link:
   `curl -X POST https://<CADDY_DOMAIN>/api/rooms -H "X-Admin-Password: <ADMIN_PASSWORD>" -H "content-type: application/json" -d '{"name":"Lunch"}'`.
6. Members open `/join/<invite_token>`, set a nickname + PIN, and fill in their
   bank details on the profile screen. Add placeholders ahead of time with
   `@bot thêm thành viên …`; they claim them on first sign-in.
7. Nightly backups: schedule `deploy/backup.sh` from cron (see the script header).

## Testing

- **Unit** (`pytest`): money math (shares sum-exactly incl. payer-not-participant
  and guests, negative/overshoot rejection, remainder; greedy netting), period
  boundaries, ledger balances incl. `since_last`, roster/account resolution,
  join/identify/claim, draft lifecycle, QR encoding, tools, image sanitize,
  `@bot` mention detection, SSE/`agui` translation, `RoomHub`, a mocked agent
  turn, and API routes (golden fixtures under `backend/tests/golden/`).
- **Frontend** (`vitest`): SSE parsing, message merge/dedupe, agent-timeline,
  balance-table, expense-draft-card, and mention rendering.
- **E2E** (manual): deploy, join two devices to a room, run a meal log (inline
  photo) → edit/confirm the draft → a preview → a `chốt`; verify the QR images
  render in the card and the ledger persists across a container restart.

**CI/CD** (GitHub Actions, `.github/workflows/`): `ci.yml` runs on every push
and PR — backend `pytest` (Python 3.11 + 3.12), frontend `tsc --noEmit` +
`vitest`, and a build of both production Docker images. `deploy.yml` redeploys
to the droplet over SSH (`git pull && docker compose up -d --build`) on merges
to `main`; it is a no-op until the `DEPLOY_SSH_KEY` / `DEPLOY_HOST` secrets are
set (see the workflow header). The opt-in LLM eval (`RUN_LLM_EVAL`) is not run
in CI.

## Out of scope (documented follow-ups)

Auth/RBAC beyond invite-link + PIN and the admin room-create password;
per-dish itemization; multi-currency / e-wallets; fixed weekly cadence/cron;
push notifications; a warm persistent Cursor bridge / horizontal scaling
(the single-process ledger writer + in-process SSE hub would need a redesign).
