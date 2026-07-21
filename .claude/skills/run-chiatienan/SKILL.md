---
name: run-chiatienan
description: Run, start, launch, or screenshot the chiatienan lunch-splitting app locally — the FastAPI backend + Next.js frontend together, wired via a dev proxy. Use when asked to run/start/serve/preview/smoke-test the app or drive its chat UI.
---

# Run chiatienan locally

chiatienan is a two-part web app: a **FastAPI backend** (API only) and a
**Next.js frontend** (the chat PWA). In production Caddy routes `/api/*` +
`/internal/*` to the backend and everything else to the frontend. Locally,
`run.sh` starts both and the frontend's dev-only proxy (in
[`frontend/next.config.ts`](../../../frontend/next.config.ts)) plays Caddy's
routing role.

Paths below are relative to the repo root. Driver lives at
`.claude/skills/run-chiatienan/`. Authored + verified on **macOS**; commands
assume `python3` (≥3.11) and `node`/`npm` on PATH.

## Run (agent path)

One command. It sets up deps on first run, picks free ports (the dev box often
already has things on 3000/3001/8000), starts both servers in the background,
and prints the invite URL to open:

```bash
bash .claude/skills/run-chiatienan/run.sh
```

Output ends with a block like:

```
  Frontend : http://localhost:3002
  Backend  : http://127.0.0.1:8000   (/health, /api/*)
  Open the app (join the demo room, pick a nickname + PIN):
    http://localhost:3002/join/<invite-token>
```

Stop both servers (also called automatically at the start of each `run.sh`, so
re-running is safe):

```bash
bash .claude/skills/run-chiatienan/stop.sh
```

### Drive the UI

Open the printed `/join/<token>` URL in the Browser pane (`preview_start`), then
drive it with the browser tools. The join → chat flow that works:

1. Fill **Tên hiển thị / Biệt danh / PIN** (bank fields optional) and click
   **Tạo & vào phòng** → lands in the room chat.
2. Type in the composer ("Nhắn tin…") and click **Gửi** → the message appears
   on the right; it round-trips through the API and back over SSE.
3. `screenshot` to verify. A real render shows the header ("chiatienan", member
   chip, Light mode / Hồ sơ / Đăng xuất) and the composer bar.

### Smoke the API (no browser)

```bash
curl -s http://127.0.0.1:8000/health                      # {"status":"ok"}
curl -s http://127.0.0.1:3002/api/rooms/<invite-token>    # room JSON via the proxy
```

The second call proving non-empty JSON is the key check: it confirms the
frontend→backend proxy is live (not just the frontend serving its own 404).

## Test

Backend unit suite (no network / SDK calls; 150 tests, ~2s):

```bash
cd backend && .venv/bin/pytest -q
```

## Gotchas

- **The frontend has no backend without the dev proxy.** The app fetches
  relative `/api/*` paths. `frontend/next.config.ts` must contain the
  `rewrites()` block that forwards `/api/*` + `/internal/*` to `BACKEND_ORIGIN`
  (default `http://127.0.0.1:8000`), gated to skip when `NODE_ENV=production` so
  Caddy stays the only router in prod. `run.sh` passes `BACKEND_ORIGIN`; without
  the block, joining a room silently fails. Keep that block in the repo.
- **Ports 3000/3001/8000 are often already taken** (Docker/Open WebUI/Langfuse
  on this machine). `run.sh` scans upward for free ports — that's why the
  frontend commonly lands on 3002+. Read the printed URL; don't assume 3000.
- **`DATABASE_URL` default is `/data/...`** (the container volume) and isn't
  writable locally. `run.sh` overrides it to `data/chiatienan.db` under the repo
  (gitignored). Delete `data/` to reset state.
- **`@bot` mentions need `CURSOR_API_KEY`.** Without it the bot turn errors and
  posts "⚠️ Bot gặp lỗi"; plain messages, drafts, profile, and live updates all
  work. To run the bot locally: `CURSOR_API_KEY=… bash .claude/skills/run-chiatienan/run.sh`.
- **Only the demo room is auto-created.** `run.sh` creates one room ("Lunch
  (local)") via the admin endpoint (password `devpass`) and prints its invite.
  More rooms: `POST /api/rooms` with header `X-Admin-Password: devpass`.

## Troubleshooting

- **Join form does nothing / room won't load** → the proxy isn't wired. Confirm
  the `rewrites()` block is in `frontend/next.config.ts` and re-run `run.sh`.
- **`ModuleNotFoundError: uvicorn` (or the venv is incomplete)** → the venv was
  only partially installed. `run.sh` installs on first run, or force it:
  `cd backend && .venv/bin/pip install -e ".[test]"`.
- **`EADDRINUSE`** → a stale server is holding the port. `bash
  .claude/skills/run-chiatienan/stop.sh`, then re-run.
- **Server didn't come up** → read `data/backend.log` / `data/frontend.log`.
