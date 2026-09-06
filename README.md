# chiatienan — lunch-splitting PWA

[![CI](https://github.com/emismith90/chiatienan/actions/workflows/ci.yml/badge.svg)](https://github.com/emismith90/chiatienan/actions/workflows/ci.yml)

A self-hosted chat app (installable PWA) for a group of ~6–7 colleagues who eat
lunch together. Everyone joins a shared **room** and chats in Vietnamese. When
someone pays, they `@mention` the bot — named **Phoenix**, because it was
reborn on a new LLM engine (`@bot` still works as a legacy alias) — with a
short natural-language message
(optionally a bill photo). The bot interprets it with an LLM and posts an
**editable expense-draft card**; a human confirms it, and only then is the meal
written to an append-only ledger. On demand (*"@phoenix ai trả tuần này"*) it nets
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
   room chat ── @phoenix ────────┤  chat.py     @phoenix detect + dispatch (serialized)
   live updates ◀── SSE ──────────┤  realtime.py in-process RoomHub pub/sub
                                   │  kernel.py   kernos pipeline: the turn as ordered plugins
                                   │  agent.py    shim → kernos.engine.pi → agent_sidecar (Node, Pi)
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
  `@phoenix` fires a **background agent turn**. Clients subscribe to
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
| `chat.py` | Persist/list messages, `@phoenix` detection, agent dispatch (serialized), deterministic bot-reply rendering |
| `drafts.py` | Draft-card lifecycle, generic over the packs' `DraftKind`s: persist, edit, commit, supersede, cancel |
| `tools.py` | The host's tool composition point: `ToolContext`, `CustomTool`, `build_tools` (the enabled packs' tools in the legacy order), `tool_manifest` |
| `packs/` | this host's packs: the registration of the framework's `lunch_ledger` (QR builder + place resolver injected), `lunch_places` (restaurants, memos), `room_members` (member CRUD) |
| `prompt.py` | Vietnamese-aware system prompt + tool guidance |
| `images.py` | Inline-image sanitize (vision) |
| `qr.py` | VietQR image URL builder (pure, no network) |
| `kernel.py` | kernos composition root: registry of plugins, host adapters, resolver → the pipeline `chat.run_bot_turn` runs |
| `plugins/` | this app's pipeline plugins: persona prompt, the `run_turn` seam, the money validators (fabricated commit, unbacked amounts) |
| `hostadapters.py` · `default_profile.py` | the kernos host adapters over this app's modules; the seeded default profile (today's bot as a `ProfileSpec`) |
| `agent.py` | shim over `kernos.engine.pi.PiEngine`: builds the `EngineSpec`, executes tools, logs one line; `run_turn`'s signature is frozen |
| `pi_bridge.py` | shim over `kernos.engine.pi.PiBridge`: sidecar path, our key name, `PI_*` defaults, the per-process singleton |
| `agent_sidecar/` | **Node.** Owns the whole Pi harness: provider, session, event normalization, turn caps, answer assembly |
| `realtime.py` | In-process `RoomHub` pub/sub feeding the SSE streams |
| `pi_smoke.py` | Guarded sidecar liveness check (B3) |

### The lunch ledger as a pack (`backend/packs/lunch_ledger/`)

The money tools (find/propose/void/period/statement/summary/settle/payment/draw/cancel),
the outcome decision and the deterministic reply bodies, the two draft kinds and the
eval-world fixtures — importing `kernos` and `ledger_core` only. What it needs from a
host is injected: the card store, clock and draw on the per-turn context, the QR builder
and place resolver at registration (`app/packs/lunch.py`). `tests/test_lunch_ledger_pack.py`
runs it against a stub host; `ledger_core/` is the money domain underneath (meals,
payments, FIFO debt edges, netting, periods, VietQR) that lunch and poker share.

### A second business: the poker ledger (`backend/packs/poker_ledger/`)

Boot seeds a second business, `poker` (agent `dealer`), next to lunch. Bind a room to it
(`PUT /api/admin/spaces/{room_id}/binding`) and the bot records game nights instead of
meals: every player's buy-in and cash-out, chips conserved exactly (rake and tips as an
explicit `house`), debts from losers to winners settled with the same VietQR flow. Both
businesses share `backend/packs/ledger_tools` (statements, settlement, payments, the
random draw) over `backend/ledger_core`. The frontend has no bespoke card for a game
draft: it renders through the generic `DraftCard` (title, the payload's fields, Confirm /
Cancel), which any registered `*_draft` kind falls back to.

### The agent kernel (`backend/kernos/`)

`kernos` is a host-agnostic framework the bot now runs on — a turn pipeline with
typed stages, a plugin registry with schema-validated configs, an `Engine`
protocol (Pi is the first engine), host adapter protocols, and a versioned
`ProfileSpec`. chiatienan is its first host; `tests/test_layering.py` enforces that
nothing under `kernos/` imports the app. Design:
[`docs/superpowers/specs/2026-09-05-agent-cms-design.md`](docs/superpowers/specs/2026-09-05-agent-cms-design.md);
plan: [`docs/superpowers/plans/2026-09-05-agent-os-framework.md`](docs/superpowers/plans/2026-09-05-agent-os-framework.md).
The content plane (Phase 2) is live: the bot's prompt, rules, skills, models, caps and
pipeline are versioned content in `kn_` tables, seeded from code on first boot and
re-synced while unedited. The admin API under `/api/admin/*` (admin password) edits
sources with ETags, drafts and publishes versions through the gates (schema,
money-safety, model probe, reflexivity), binds a room to an agent, and shows what a
room runs at `GET /api/admin/spaces/{room_id}/resolved`. Rooms nobody binds keep
running the seeded default, byte for byte.
Every turn leaves a trace row (`kn_turn_traces`: the plugins that ran, each tool call
with its arguments and result, a summary) — `GET /api/admin/spaces/{room_id}/turns` and
`…/turns/{turn_id}`; a turn that raised is traced with its error. Retention is the
`keep_days` config of the `kernos.after.trace` plugin (30 by default).
Eval is content too: cases, suites, rubrics and runs under `/api/admin/businesses/{id}/eval/*`
and `/api/admin/eval/runs`; `POST …/businesses/{id}/eval/import` loads the benchmark's
`typical` corpus as the `lunch-typical` suite, `POST …/profiles/{id}/versions/{v}/eval?suite=`
starts a run as a background job (`python -m app.evalhost run …`), and publishing a
version whose spec names `eval.suites` requires a completed run of the same content that
passed every blocking grader (`backend/kernos/eval/`, `backend/app/evalhost.py`).
Collections are the data plane: `PUT /api/admin/businesses/{id}/collections/{slug}` defines a
schema-validated document type (JSON Schema in the sidecar-safe subset), documents live per
room under `/api/admin/spaces/{room_id}/collections/{slug}/documents`, and a profile that
enables the `collections` pack gets `{slug}_find` / `{slug}_upsert` / `{slug}_delete` tools
generated from the definition (`backend/kernos/data/`).
Agents: a business has one default manager agent (the one an unbound room runs) and may add
`sub` agents (`POST /api/admin/agents`, `role: "sub"`, an optional `description`); a manager
whose `delegates_to` lists sub ids gets an `ask_<sub_slug>(task)` tool per sub, which runs
the sub's profile as a nested turn in the same room within the manager's remaining time and
tool budget and hands back its text and structured tool results (`backend/kernos/agents.py`).
The sub's results back the manager's numbers, its text never does, and only the manager's
own `propose_*` calls make cards. The seeded agents delegate to nobody, so a room behaves
exactly as before until an admin adds a sub.
Self-administration is opt-in twice: a profile enables the `os_admin` pack and the agent's
`capabilities` grant verbs (`read` its configuration, traces and eval results; `draft` a change to
its own prompt, skills or non-money rules and propose it; `eval` start a suite as a job and add a
review case; `publish` itself, only inside its `self_change_scope`, never on the blacklist, and only
with a finished eval run of the exact content). Proposals live at `/api/admin/proposals` and are
approved or rejected there by a person; approval publishes through the gates and writes the sources
so the change survives the next draft. Nothing a `cms_*` tool returns can back a number in a reply
(`backend/kernos/osadmin.py`). The steward brief is at `GET /api/admin/steward/brief`.
Portability (Phase 9): `app/kernel.py` is a subclass of the framework's `kernos.host.BaseKernel`
with four host hooks; `examples/minimal_host/host.py` is a second host with no chiatienan module
on its path (its test guards that), running the framework run stage `kernos.run.engine` over a
scripted engine and streaming AG-UI events (`kernos.api.agui`). A published profile exports as a
Pi package (`GET /api/admin/profiles/{id}/export` — skills, prompts, `AGENTS.md`,
`.pi/settings.json`, and `kernos.json` for a lossless re-import) and a package imports as sources
and a draft, never a publish (`POST /api/admin/businesses/{id}/import`). The sidecar's npm package
is `kernos-sidecar`; the directory keeps its name until the framework is split out.

The steward (off by default): every boot seeds a `steward` sub-agent with a profile of its
own — one pack (`os_admin`), read and draft capabilities, and the lunch profile under its
stewardship. Nothing points at it, so no room's tool list changes. Connect it with one call
(`PATCH /api/admin/agents/<phoenix id>` with `{"delegates_to": [<steward id>]}`) and Phoenix
gains a single `ask_steward` tool; asking it makes the steward read `cms_get_friction` — six
deterministic detectors over the room's stored turn traces — and, when a pattern is clear,
draft one change to a skill, rule or the prompt and open a proposal. It publishes nothing:
a person approves at `POST /api/admin/proposals/<id>/approve`, or confirms the proposal card
in the room where the agent that wrote it runs. Before turning it on, read
[`docs/superpowers/plans/2026-09-06-deploy-runbook.md`](docs/superpowers/plans/2026-09-06-deploy-runbook.md).

The room's own CMS (Phase 11): every member sees the agent their room runs under a **Bot**
tab — the system prompt, the skills, the rules, and a revision log naming who changed what.
A room with its own binding can edit the prompt, the skills and the non-money rules, and
republish any earlier version; a room without one is read-only, because `POST
/api/rooms/create` is public and an unbound room resolves to the shared default agent, so
membership cannot be a permission. Never editable from a room: the model, the caps, the
pipeline, the tool packs, the builtin tools and any rule tagged `money`. Two limits worth
knowing — the first member edit stops that profile following deploys (`managed_by`), and a
prompt-level money rule is advisory: with `bash` enabled, prose can still talk the model
into arithmetic and `backed_amounts` counts a builtin's output as evidence. The enforced
invariants are the blacklisted fields, the confirmed card a ledger write needs, and the
forged-commit guard. See
[`docs/superpowers/plans/2026-09-06-deploy-runbook.md`](docs/superpowers/plans/2026-09-06-deploy-runbook.md).

### Frontend (`frontend/src/`, Next.js 16 / React 19)

- `app/page.tsx` — the room view (or an "open an invite link" prompt when signed out).
- `app/join/[token]/page.tsx` — join screen: pick an unclaimed name or create an
  account, set a PIN, or identify with nickname + PIN.
- `components/chat/` — `room-view`, `message-list`, `composer` (with paste-to-attach
  + `mention-dropdown`), `bot-message`, `expense-draft-card`, `draft-card` (the
  generic fallback for a kind with no card of its own), `balance-table`,
  `agent-timeline` (live tool progress), `zoomable-image`.
- `hooks/use-room.ts` — SSE subscription + optimistic-send merge/dedupe by id.
- `lib/` — `api`, `sse`, `session`, `format`, `theme`, `sw-register`, `utils`.
- **PWA:** `public/manifest.webmanifest` + `public/sw.js` (registered via
  `sw-register`) make it installable to a home screen.
- **Icons:** all four (`icon-192`, `icon-512`, `apple-touch-icon`,
  `src/app/favicon.ico`) are rasterized from `scripts/icon-phoenix.svg` by
  `python scripts/gen-icons.py`. The filenames never change, so **new art must
  also bump the `?v=` on every reference** (manifest, `layout.tsx`, `sw.js`) —
  browsers, the SW cache and an installed Android WebAPK all decide "is this a
  new icon?" from the URL alone. `src/app/__tests__/icons.test.ts` enforces it.

## Usage (in the room chat, mention `@phoenix`)

- Log a meal: `@phoenix 840k cả nhóm trừ An, Bình +50k` (± a pasted bill photo) →
  posts an **editable draft card**; tap to adjust payer/participants/total, then **Confirm**.
- Payer didn't eat: `@phoenix An trả 200k nhưng không ăn, chia Bình và Cường`
- Correct a recorded meal: `@phoenix xoá 42`
- Preview who-owes-whom: `@phoenix ai trả tuần này`
- Lock it in (the only thing that closes a period): `@phoenix chốt tuần này`
- Display-only spend: `@phoenix tháng này tôi tiêu bao nhiêu`
- Manage members: `@phoenix thêm thành viên Dũng`, `@phoenix đổi tên An thành Anh`,
  `@phoenix xoá thành viên Cường` (soft-delete), `@phoenix khôi phục Cường`.
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
# terminal 1 — backend (OPEN_ROUTER_KEY only needed for actual @phoenix turns)
cd backend && cp ../.env.example ../.env   # then edit ../.env
OPEN_ROUTER_KEY=… ADMIN_PASSWORD=… uvicorn app.main:app --reload

# terminal 2 — frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
npm test                                     # vitest unit suite

# Sidecar (Node) — CI runs this on EVERY change, because Python builds the tool
# schemas and the sidecar converts them: when they drift the failure is not a red
# build, it is the model sending arguments the tool rejects. Without `npm ci`,
# `node --test` fails module-not-found and gives no signal at all.
cd backend/agent_sidecar && npm ci && node --test
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
| `OPEN_ROUTER_KEY` | OpenRouter key for the sidecar (note the name — not `OPENROUTER_API_KEY`) |
| `PI_MODEL` | default `~deepseek/deepseek-v4-flash-latest` (text-only) |
| `PI_VISION_MODEL` | default `qwen/qwen3-vl-30b-a3b-instruct`. Mandatory in practice: every bill photo routes here |
| `PI_MAX_TOOLS` / `PI_MAX_SECONDS` | per-turn runaway caps (40 / 120 s). A breach is a partial answer, not an error |
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

4. Validate the agent sidecar runs in-container (B3):
   `curl -X POST https://<CADDY_DOMAIN>/internal/bridge-smoke -H "X-Admin-Password: <ADMIN_PASSWORD>"`.
5. Create the first room and share its invite link:
   `curl -X POST https://<CADDY_DOMAIN>/api/rooms -H "X-Admin-Password: <ADMIN_PASSWORD>" -H "content-type: application/json" -d '{"name":"Lunch"}'`.
6. Members open `/join/<invite_token>`, set a nickname + PIN, and fill in their
   bank details on the profile screen. Add placeholders ahead of time with
   `@phoenix thêm thành viên …`; they claim them on first sign-in.
7. Nightly backups: schedule `deploy/backup.sh` from cron (see the script header).

## Testing

- **Unit** (`pytest`): money math (shares sum-exactly incl. payer-not-participant
  and guests, negative/overshoot rejection, remainder; greedy netting), period
  boundaries, ledger balances incl. `since_last`, roster/account resolution,
  join/identify/claim, draft lifecycle, QR encoding, tools, image sanitize,
  `@phoenix` mention detection, SSE/`agui` translation, `RoomHub`, a mocked agent
  turn, and API routes (golden fixtures under `backend/tests/golden/`).
- **Frontend** (`vitest`): SSE parsing, message merge/dedupe, agent-timeline,
  balance-table, expense-draft-card, and mention rendering.
- **E2E** (manual): deploy, join two devices to a room, run a meal log (inline
  photo) → edit/confirm the draft → a preview → a `chốt`; verify the QR images
  render in the card and the ledger persists across a container restart.

**CI/CD** (GitHub Actions, `.github/workflows/`): `ci.yml` runs on every push
and PR — backend `pytest` (Python 3.11 + 3.12), frontend `tsc --noEmit` +
`vitest`, and a build of both production Docker images. `deploy.yml` (on merges
to `main`) builds both images **on the runner**, pushes them to GHCR, then
SSHes into the droplet to regenerate `.env` from GitHub secrets and
`docker compose pull && up -d` — so the 512 MB host never builds and can't OOM
on deploy. It is a no-op until the `DEPLOY_SSH_KEY` / `DEPLOY_HOST` secrets are
set (see the workflow header for the full secret/variable list). The opt-in LLM
eval (`RUN_LLM_EVAL`) is not run in CI.

## Out of scope (documented follow-ups)

Auth/RBAC beyond invite-link + PIN and the admin room-create password;
per-dish itemization; multi-currency / e-wallets; fixed weekly cadence/cron;
push notifications; horizontal scaling
(the single-process ledger writer + in-process SSE hub would need a redesign).
