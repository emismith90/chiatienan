# chiatienan — Teams lunch-splitting bot

A Microsoft Teams bot for a group of ~6–7 colleagues who eat lunch together.
When someone pays, they `@mention` the bot with a short natural-language message
(optionally a bill photo). The bot interprets it with an LLM, records the meal in
an append-only ledger, and echoes a breakdown. On demand (*"ai trả tuần này"*) it
nets everyone's balances over the requested period, produces the minimal set of
transfers, and returns a **VietQR** code per transfer so people pay by scanning.

The LLM decides *when* to call tools; **the tools own every number**. A number
may flow user → LLM → tool **once** (as input), but never tool → LLM → tool — so
no amount that ends up in a QR is ever computed or transcribed by the model.

> Design: [`docs/superpowers/specs/2026-07-20-chiatienan-teams-lunch-bot-design.md`](docs/superpowers/specs/2026-07-20-chiatienan-teams-lunch-bot-design.md)

## Architecture

```
Teams group ─@mention + text/photo─▶ Caddy (TLS) ─▶ FastAPI backend (Python)
   ◀── breakdown / settlement + QR ── (proactive) ──   teams.py     botbuilder adapter
                                                        worker       async turn queue (concurrency 1)
Admin browser ─▶ /admin (password) ─▶ Caddy ───────▶   agent.py     Cursor SDK (LLM + tools)
                                                        tools.py     CustomTools (all arithmetic)
                                                        ledger/roster/qr/money/periods
                                                                     SQLite (WAL) on a volume
```

- `/api/messages` **acks 200 immediately** and enqueues the turn (a full agent
  run exceeds the Bot Connector's ~15 s timeout). A single worker runs the agent
  to completion and **replies proactively** via the saved `ConversationReference`.
- Each processed `activity.id` is recorded, so a re-delivered activity never
  writes a second meal.
- Money-bearing replies (meal breakdown, settlement card) are rendered
  deterministically from the tool results — never from the model's text.

### Backend modules

| Module | Responsibility |
|--------|----------------|
| `config.py` | Env settings |
| `money.py` | `split_shares` (equal base + signed overrides, remainder rule) + `net_transfers` (greedy) |
| `periods.py` | ICT period math (`since_last`, `this_week`, …; week = Mon–Sun) |
| `models.py` / `db.py` | SQLAlchemy models + WAL sqlite engine |
| `ledger.py` | Append-only meals+shares, void, balances, settlements log, dedup |
| `roster.py` | Member CRUD, name/alias/mention resolution, identity capture |
| `qr.py` | VietQR image URL builder |
| `tools.py` | The LLM-facing `CustomTool` set |
| `prompt.py` | Vietnamese-aware system prompt |
| `images.py` | Inline-image sanitize (vision) |
| `cursor_runner.py` / `agent.py` | Cursor SDK model wiring + run-to-completion |
| `reply.py` | Teams text + Adaptive Card from tool results |
| `teams_parse.py` / `teams.py` | Activity parsing + botbuilder wiring |
| `worker.py` | Async turn queue (concurrency 1), idempotency |
| `admin.py` / `main.py` | `/admin` roster page + app assembly (`/health`, `/api/messages`) |

## Usage (in the Teams group chat)

- Log a meal: `@chiatienan 840k cả nhóm trừ An, Bình +50k` (± a pasted bill photo)
- Payer didn't eat: `@chiatienan An trả 200k nhưng không ăn, chia Bình và Cường`
- Correct: `@chiatienan xoá 42`
- Preview who-owes-whom: `@chiatienan ai trả tuần này`
- Lock it in (the only thing that closes a period): `@chiatienan chốt tuần này`
- Display-only spend: `@chiatienan tháng này tôi tiêu bao nhiêu`

Bill photos must be **pasted inline** — file attachments go to SharePoint and
aren't retrievable with the bot token (the bot will ask you to paste inline).

## Local development

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest -q                      # runs the full unit suite (no network / SDK calls)
```

To run the API locally (bot endpoint needs Entra creds; `/health` + `/admin`
work without them):

```bash
cp ../.env.example ../.env      # then edit ../.env
CURSOR_API_KEY=… ADMIN_PASSWORD=… uvicorn app.main:app --reload
```

## Configuration

Copy `.env.example` → `.env` and fill it in. Key vars:

| Var | Purpose |
|-----|---------|
| `CURSOR_API_KEY` | Cursor SDK (a **user/service-account** key — not a Team Admin key) |
| `CURSOR_SDK_MODEL` | default `composer-2.5` (vision-capable) |
| `MICROSOFT_APP_ID` / `MICROSOFT_APP_PASSWORD` | Entra bot registration |
| `MICROSOFT_APP_TYPE` / `MICROSOFT_APP_TENANT_ID` | `SingleTenant` + Niteco tenant id |
| `DATABASE_URL` | `sqlite:////data/chiatienan.db` (absolute, on the volume) |
| `TZ` | `Asia/Ho_Chi_Minh` |
| `ADMIN_PASSWORD` | `/admin` auth |
| `QR_BASE_URL` / `QR_TEMPLATE` | VietQR image endpoint + template |
| `CADDY_DOMAIN` | droplet domain for TLS + the Teams messaging endpoint |

## Deploy (DigitalOcean droplet)

Full runbook: [`deploy/README.md`](deploy/README.md). In short:

1. Provision a droplet (**≥ 1 GB RAM**) with Docker + Compose, point an A-record
   at it (`CADDY_DOMAIN`).
2. Clone the repo, `cp .env.example .env`, and fill in `.env`.
3. Bring it up from the repo root (Caddy fetches TLS automatically):

   ```bash
   docker compose up -d --build
   ```

4. Validate the Cursor SDK bridge runs in-container (B3):
   `curl -X POST https://<CADDY_DOMAIN>/internal/bridge-smoke -H "X-Admin-Password: <ADMIN_PASSWORD>"`.
5. Register the Azure Bot resource's messaging endpoint as
   `https://<CADDY_DOMAIN>/api/messages`.
6. Open `https://<CADDY_DOMAIN>/admin`, add the roster (names, aliases, bank
   details). Members the bot captures on first mention appear here (highlighted)
   for you to complete + activate.
7. Nightly backups: schedule `deploy/backup.sh` from cron (see the script header).

### Teams app package

See [`teams-app/README.md`](teams-app/README.md): fill the `manifest.json`
placeholders (`MICROSOFT_APP_ID`, domain), zip with the icons, and sideload into
the lunch group chat. Requires `scopes: ["groupChat"]` and Teams-admin approval
for custom-app sideloading.

## Testing

- **Unit** (`pytest`): money math (shares sum-exactly incl. payer-not-participant,
  negative/overshoot rejection, remainder; greedy netting), period boundaries,
  ledger balances incl. `since_last`, idempotency, roster resolution, QR encoding,
  tools, image sanitize, mention stripping, activity parsing, reply/card build,
  worker dedup/fallback, and a mocked agent turn.
- **E2E** (manual, per design §10): deploy, sideload into a test group, run a log
  (inline photo) + a preview + a `chốt`; verify the QR images render in the card
  and the ledger persists across a container restart.

## Out of scope (documented follow-ups)

Auth/RBAC beyond the admin password; per-dish itemization; multi-currency /
e-wallets; file-attachment bill photos (SharePoint/Graph); fixed weekly
cadence/cron; a warm persistent bridge / horizontal scaling.
