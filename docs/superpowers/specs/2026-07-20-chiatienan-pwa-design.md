# chiatienan — Lunch-Splitting PWA — Design

**Date:** 2026-07-20
**Status:** Approved design — ready for implementation
**Supersedes:** `2026-07-20-chiatienan-teams-lunch-bot-design.md` (Teams channel abandoned — IT
would not approve a personal custom Teams app / sideloading).
**Repo:** `chiatienan` — GitHub `emismith90/chiatienan`; deployed on a DigitalOcean droplet.

---

## 1. Context & Goal

A ~6–7 person group eats lunch together; group size varies day to day, and anyone can be the payer.
The **Teams-bot channel was abandoned** because IT won't approve a personal custom app or enable
sideloading on the Niteco tenant. We pivot to an **installable PWA**: an independent web app the
group installs on their devices, with no dependency on any corporate tenant, Azure, or IT.

**The deterministic money engine and the Cursor-SDK agent built for the Teams version are reused
wholesale** — only the channel (Teams gateway) is replaced by a multi-user web chat.

**Goal:** an admin creates a **room** and shares an **invite link**. Anyone with the link joins,
creates a lightweight account (name, nickname, banking, PIN), and lands in a shared chat. Members
chat freely; the **agent only acts when `@bot`-mentioned** (log a meal from text/photo, show a
period settlement with VietQR). All money math stays deterministic (in tools), never done by the LLM.

**Non-goals (YAGNI):** no corporate SSO, no bank-grade security (see §6), no per-dish itemization,
no multi-currency, no fixed cadence.

---

## 2. Key Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Channel: installable PWA** (Next.js frontend + FastAPI backend), no Teams/Azure/IT | IT blocked the Teams path; a PWA is fully self-owned. |
| D2 | **Reuse the sample's chat flow** (AG-UI streaming + renderers) as the primary UI | It already does streamed chat, tool-call timeline, image attach, markdown/chart renderers. |
| D3 | **Reuse the whole deterministic core + agent** unchanged | `money/ledger/periods/qr/tools/agent/cursor_runner/models/db/prompt/images` are channel-agnostic. |
| D4 | **Multi-room**; all ledger data scoped by `room_id` | One server can host several independent groups. |
| D5 | **Studio-apartment model: flat, no per-room admin.** Server `ADMIN_PASSWORD` only *mints new rooms*; inside a room **every resident is equal** — anyone with the invite link (the door key) can add members (via the agent), log meals, and query. | Matches the group's reality; the link is the trust boundary, so a privileged admin role adds nothing. Room creation stays gated to stop strangers spamming rooms on the public URL. |
| D5a | **Members can be added two ways:** (a) self-register via the link; (b) **`@bot add <name> …`** creates an *unclaimed* profile (no PIN). | Onboarding is conversational; a resident can pre-add a friend's name+bank, who later claims it. |
| D6 | **Realtime via SSE fan-out** (per-room, in-process pub/sub) | Server pushes every message (human + bot) to all members live; single-instance makes in-proc pub/sub sufficient. |
| D7 | **Dual-key identity = invite link (shared door key) + personal PIN** (not authentication) | The link gets you into the apartment; the PIN identifies *which resident* you are (attribution + which bank to use) and lets you resume on another device. A VN bank number + holder is a receive-only "payment address," not sensitive. `pin` is **nullable** until a profile is claimed (agent-added profiles start unclaimed). |
| D8 | **No security machinery** (no PIN hashing, rate-limiting, or lockouts) | There is no privileged data to guard behind the link; those measures would protect nothing. HTTPS + link secrecy is the model. |
| D9 | **Agent acts only on `@bot`** | The room is a human chat with a bot participant invoked on demand. |
| D10 | **Deploy: existing droplet + Docker + Caddy + TLS**, add a frontend container | Reuses the validated infra (B3 cursor-sdk bridge confirmed); Caddy path-routes `/` → frontend, `/api` + SSE → backend. |

---

## 3. Architecture

```
Installed PWA (phone/laptop)
  │  open invite link → create account (name, nickname, bank, PIN) → chat
  ▼
Caddy (TLS · chiatienan.duckdns.org)
  ├── /                → frontend  (Next.js PWA)
  └── /api/*, SSE      → backend   (FastAPI)
                           rooms.py      room CRUD + invite tokens (admin-gated create)   NEW
                           accounts.py   join/create-account, identify(nick+pin), profile  NEW
                           chat.py       message store + post; @bot detection → agent      NEW
                           realtime.py   per-room in-proc pub/sub + SSE fan-out            NEW
                           agent.py + cursor_agui  Cursor SDK run, streamed as AG-UI    REUSE/adapt
                           tools/ledger/money/periods/qr/models/db/prompt/images        REUSE
                                              SQLite (room-scoped) on the droplet volume
```

**Reused backend:** `money.py`, `ledger.py`, `periods.py`, `qr.py`, `tools.py`, `agent.py`,
`cursor_runner.py`, `models.py`, `db.py`, `prompt.py`, `images.py`, `config.py`, `bridge_smoke.py`.
**Dropped:** `teams.py`, `worker.py`, `reply.py`, `teams_parse.py`, `teams-app/`, and the Teams-era
`admin.py` roster page (replaced by room/account self-service APIs).
**New:** `rooms.py`, `accounts.py`, `chat.py`, `realtime.py`, a session dependency, and the frontend.

**Streaming note (amended after review):** the bot does **not** token-stream. A `@bot` turn runs
`agent.run_turn` **to completion** (reusing the existing run-to-completion agent — no `cursor_agui`
adaptation), then persists **one** `room_message` (kind=`bot`) whose numbers come straight from the
tool results (`settle_period`/`record_meal`). The room SSE carries a `bot.typing` event when the run
starts and a `bot.done` when it finishes, with the final bot `message` in between. This is simpler
and keeps money numbers out of any LLM-streamed text (D3).

---

## 4. Data Model (SQLite/WAL via SQLAlchemy)

All timestamps ICT (`Asia/Ho_Chi_Minh`); week = Mon–Sun. New/changed tables:

- **`rooms`**: `id, name, invite_token (unguessable), created_at`.
- **`accounts`** (a room member): `id, room_id (fk), display_name, nickname, bank_code,
  account_number, account_holder, pin (nullable), created_at`. `(room_id, nickname)` unique. `pin`
  stored plain (D8 — not a secret; identity mapping only). `pin IS NULL` ⇒ **unclaimed** (agent-added,
  awaiting first-time claim). Claiming = setting the PIN for an unclaimed nickname.
- **`sessions`**: `id, account_id (fk), token, created_at` — one per device; no expiry.
- **`room_messages`**: `id, room_id (fk), author_account_id (null = bot), kind (text|bot),
  body, attachments (json), created_at`. The shared chat log; ordered by id.
- **Existing `meals`, `meal_shares`, `settlements` gain `room_id`** and reference `accounts`
  (formerly `members`). Ledger queries + tools all filter by the current room.

Balances stay **derived** per room + period (paid − consumed), append-only meals, void-not-mutate,
append-only `settlements` for "since last settlement" — unchanged from the prior design.

---

## 5. API & Realtime

**Rooms / accounts / auth**
- `POST /api/rooms` — **admin-password** → `{room, invite_link}`.
- `GET  /api/rooms/{invite_token}` — public room summary (name) for the join screen.
- `POST /api/rooms/{invite_token}/accounts` — self-register (name, nickname, bank, PIN) → session token.
- `POST /api/rooms/{invite_token}/identify` — nickname + PIN → session token. If the nickname is an
  **unclaimed** profile (`pin IS NULL`), this **claims** it (sets the PIN) and returns a session.
- (No HTTP route to add members — that's the agent `add_member` tool, callable by any resident.)
- `GET/PUT /api/me` — read/update own profile + banking (session token).

**Chat / realtime**
- `GET  /api/rooms/{id}/messages?since=<id>` — page history (also used for SSE catch-up).
- `POST /api/rooms/{id}/messages` — persist a message → publish to subscribers. If it `@bot`-mentions,
  enqueue an **agent run** (concurrency 1 → serializes ledger writes).
- `GET  /api/rooms/{id}/members` — room roster `[{id, display_name, nickname}]` (banking omitted).
- `GET  /api/rooms/{id}/stream?since=<id>` — authenticated **SSE**; emits `message` (human or bot),
  `bot.typing`, `bot.done`, and a periodic `: ping` heartbeat (~25 s) so dead connections are
  detected and the client reconnects. `since` lets a reconnecting client catch up gap-free; on
  subscriber-queue overflow the server **closes the stream** (client reconnects with `since`) rather
  than silently dropping events.

**Chat images:** a message may carry image attachments; they are validated by `images.sanitize_images`
and **persisted on the `room_message`** (so all members see the bill photo in history), in addition
to being passed to the agent on a `@bot` turn.

Auth = `Authorization: Bearer <session token>`; every room route checks the session belongs to that
room. Invite token and session token are bearer secrets over HTTPS (D7/D8).

---

## 6. Security posture (explicit, deliberately light)

The **invite link is the trust boundary**; everyone in a room has equal read access. Bank details
are receive-only payment addresses (not sensitive in VN). The PIN is an **identity handle, not a
password** → no hashing, no rate-limiting, no lockout. Known, accepted limitation: someone who has
the link *and* knows your nickname+PIN could pose as you — a non-issue for a trusted 7-person group.
Protections we *do* keep: HTTPS everywhere, unguessable invite/session tokens, admin-gated room
creation, and banking not shown in other members' profiles (it only surfaces via a settlement QR,
which is the point).

---

## 7. Frontend (PWA — lift & adapt the sample)

**Keep** from the sample: AG-UI chat rendering, tool-call timeline, image attach/encode, markdown +
chart/table/image renderers, composer. **Replace** localStorage history with the **server room
stream** (SSE + `/messages`). **Add** screens: join / create-account, nickname+PIN identify,
profile & banking editor, room view (member list, shared log). **Add PWA**: web app manifest +
service worker for install-to-device (app icon, standalone display; offline not required — chat
needs network).

---

## 8. Agent behavior

Runs **only when `@bot`-mentioned**. Same deterministic tools, now **room-scoped** (the run carries
`room_id` in tool context; `find_members`/`record_meal`/`settle_period`/… operate within that room's
`accounts`/ledger). A new **`add_member`** tool lets any resident onboard someone by chat
(`@bot add Bình, VCB 123, Bình Nguyen`) — it creates an **unclaimed** account (PIN null) in the room.
The reply is posted as one bot message to all members; numbers never round-trip tool→LLM→tool
(composite `settle_period`), money math stays in code.

---

## 9. Deployment

Add a **`frontend`** service (Next.js, Dockerfile lifted from the sample) to `docker-compose.yml`.
Caddy path-routes: `/` → `frontend:3000`, `/api/*` and the SSE path → `backend:8000`. Existing
droplet, TLS, SQLite volume, and swap all stay. No Azure/IT/Teams. `.env` loses the `MICROSOFT_*`
vars; keeps `CURSOR_API_KEY`, `ADMIN_PASSWORD`, `DATABASE_URL`, `TZ`, `QR_*`, `CADDY_DOMAIN`.

---

## 10. Testing

- **Unit (pytest):** reused ledger/money/periods/qr/tools tests (now with `room_id`); room + account
  creation; nickname+PIN identify; message post + `@bot` detection; SSE catch-up via `since`;
  room-scoping isolation (room A can't read room B's ledger).
- **Integration:** create room → join two accounts → post messages → both receive via SSE; `@bot`
  log a meal (with a photo) → breakdown streams to both; `@bot` settle → QR renders.
- **E2e (manual):** on the droplet, install the PWA on a phone, join via link, log + settle.

---

## 11. Out of Scope (follow-ups)

- Corporate SSO / real authentication; bank-grade security.
- Per-dish itemization; multi-currency; e-wallets (VietQR personal bank only).
- Offline mode; push notifications.
- Cross-room global accounts (accounts are per-room for now).
- Horizontal scaling (single instance — required by the in-proc SSE pub/sub).
