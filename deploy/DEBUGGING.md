# Debugging & inspecting production — chiatienan

Practical runbook for looking at the live app when something's off (bad bot
reply, wrong balance, a user report). Companion to [`README.md`](README.md)
(the deploy runbook). **Read-only inspection first; never mutate the live
ledger without a backup.**

## 0. Reaching the box

- **Droplet:** DigitalOcean, `/opt/chiatienan`, Docker Compose (Caddy + backend + frontend), SQLite on the `./data` volume.
- **SSH to the domain, not a hardcoded IP:**
  ```bash
  ssh -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org
  ```
  The domain currently resolves to `165.22.246.208`, and it **follows the droplet** if the IP ever changes (rebuild/resize) — a hardcoded IP goes stale (that's why a dead `143.198.81.194` lingers in `~/.ssh/config`; ignore it). Confirm what it resolves to with `dig +short chiatienan.duckdns.org`.
- **If the domain resolves somewhere unexpected**, DuckDNS auto-detect may have grabbed the office egress IP — **re-pin the DuckDNS record to the droplet IP** (`165.22.246.208`). Don't switch to hardcoding the IP; fix the DNS record. As a one-off fallback while the record is wrong, you can SSH the raw IP directly.
- **Office network blocks outbound SSH** (TCP connects, banner stripped → "timed out during banner exchange"). If SSH hangs, switch to a **phone hotspot**, or use the **DigitalOcean web console** (browser terminal, pure HTTPS — always works).

## 1. Logs

From `/opt/chiatienan` on the droplet:

```bash
docker compose logs --tail=200 backend      # FastAPI + agent (bot turns, tool calls, errors)
docker compose logs --tail=200 frontend     # Next.js
docker compose logs --tail=100 caddy        # TLS / routing
docker compose logs -f backend              # follow live while reproducing
```

The backend log is where agent/tool failures and stack traces surface. A bot
turn that "did nothing" almost always left a traceback here.

## 2. Reading the conversation log & ledger (the DB)

**The droplet has no `sqlite3` binary.** Don't `apt-get install` it on the box
— copy the DB off and read it locally.

**It's WAL-mode SQLite**, so recent writes live in the `-wal` file, not the
main `.db` (which is only current as of the last checkpoint). **Copy all three
files** or you'll see stale/empty data:

```bash
# on your machine — copy each file separately (globs can trip the sandbox):
scp -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org:/opt/chiatienan/data/chiatienan.db      ./prod.db
scp -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org:/opt/chiatienan/data/chiatienan.db-wal  ./prod.db-wal
# -shm is optional (SQLite rebuilds it). Opening prod.db now replays the WAL:
sqlite3 ./prod.db "SELECT count(*) FROM room_messages;"
```

Key tables (all room-scoped by `room_id`):

| Table | What |
|---|---|
| `rooms` | one row per group; find the real group's `id` first |
| `members` | `id, room_id, display_name, nickname, active` |
| `room_messages` | **the conversation log** — `kind` (`text`/`bot`/`expense_draft`/…), `body`, `attachments` (JSON), `created_at`, `author_member_id` |
| `meals` / `meal_shares` | who paid, who ate, per-head shares (`voided` excludes) |
| `payments` | cash payments `from_member_id → to_member_id`, `amount`, `meal_id?` |
| `settlements` | committed "chốt" events (closes a period) |

**Dump the chatlog for one room, chronological, with author names:**

```bash
sqlite3 -noheader ./prod.db "
  SELECT rm.created_at || '  [' || rm.kind || ']  ' ||
         COALESCE(m.display_name,'BOT') || ':  ' ||
         replace(substr(rm.body,1,300), char(10), ' ')
  FROM room_messages rm LEFT JOIN members m ON m.id = rm.author_member_id
  WHERE rm.room_id = <ROOM_ID>
  ORDER BY rm.created_at, rm.id;"
```

Alternative (no copy): query in place via the backend container's Python,
which sees the live WAL — `docker compose exec backend python -c "..."` using
`app.db`. Copying off-box is usually simpler and keeps prod read-only.

## 3. DB dump / backup

Always back up before any write. On the droplet (there's a `backups/` dir under `data/`):

```bash
docker compose exec backend python - <<'PY'
import sqlite3, datetime, os
os.makedirs("/data/backups", exist_ok=True)
dst = f"/data/backups/backup-{datetime.date.today()}.db"
sqlite3.connect("/data/chiatienan.db").backup(sqlite3.connect(dst))
print("backed up ->", dst)
PY
```

(`.backup` is WAL-safe — it captures committed + WAL state consistently. See also `deploy/backup.sh`.)

## 4. Schema changes / deploy

The app uses SQLAlchemy `create_all()` — it **only creates missing tables/
columns on a FRESH database**. It will **not** add a new column to the existing
live DB.

- **Additive column on the live DB:** apply it by hand, non-destructively —
  **never `rm` the live DB** (that erases the group's real ledger):
  ```bash
  # back up first (§3), then:
  docker compose exec backend python -c "import sqlite3; sqlite3.connect('/data/chiatienan.db').execute('ALTER TABLE payments ADD COLUMN meal_id INTEGER')"
  ```
  (SQLite `ALTER TABLE ADD COLUMN` is cheap and non-destructive; a fresh DB gets
  the column from `create_all` automatically.)
- **Only** wipe + recreate (`rm data/chiatienan.db*` + restart) for a throwaway
  DB with no data worth keeping — never on the live group.

Standard redeploy (from `README.md`): `git pull && docker compose up -d --build`.
Backend/Caddy-only changes are fine as-is; **frontend changes** need the
build-OOM handling in `README.md` §5 (swap or build-elsewhere).

## 5. Live-verifying a frontend change (PWA cache gotcha)

chiatienan is an installable PWA with a **service worker** that caches JS
chunks. After a frontend deploy, a browser (and even a dev-server reload) can
serve **stale chunks**. Before concluding a FE fix "didn't work":

1. DevTools → Application → Service Workers → **Unregister**.
2. Application → Storage → **Clear site data** (or `caches.delete(...)`).
3. Hard reload.

Otherwise you're testing the old bundle.

## 6. The debug/export API — no SSH required

§0–2 all assume you can reach port 22. Sometimes you can't: the office LAN
blocks outbound SSH, and a sandboxed cloud agent may be restricted to HTTPS
through a filtering proxy. The export API serves the same read-only data over
the existing HTTPS surface, so **this is the fastest path to the chatlog from
anywhere**, and the only one from a locked-down agent.

Enable it by setting `DEBUG_API_KEY` in `.env` (see `.env.example`) and
restarting the backend. **Unset ⇒ every route 404s**, so a deploy that never
sets it is unchanged. Keys under 24 chars are refused (the API stays off and
logs why) so a placeholder can't quietly publish the ledger.

```bash
export H="X-Debug-Key: $DEBUG_API_KEY"
export B=https://chiatienan.duckdns.org/internal/debug

curl -sS -H "$H" $B/ping                     # key OK? row counts, log-mirror status
curl -sS -H "$H" $B/rooms                    # find the real group's room_id

# the conversation log — the thing you usually want
curl -sS -H "$H" "$B/conversation.txt?room_id=1&days=14"   # readable transcript
curl -sS -H "$H" "$B/conversation.csv?room_id=1" -o chatlog.csv
curl -sS -H "$H" "$B/conversation.csv?room_id=1&since_id=4210"  # incremental poll

# ledger tables as CSV
curl -sS -H "$H" $B/tables                   # names, row counts, redacted columns
curl -sS -H "$H" "$B/tables/meals.csv?room_id=1" -o meals.csv
curl -sS -H "$H" $B/tables/meal_shares.csv -o shares.csv

# whole DB, sanitised + WAL-safe, as ONE file (no -wal sidecar needed)
curl -sS -H "$H" $B/db -o prod-snapshot.db
sqlite3 prod-snapshot.db "SELECT count(*) FROM room_messages;"

# app log (backend logger + uvicorn tracebacks)
curl -sS -H "$H" "$B/logs?lines=300"
```

**What is redacted, always.** This surface exports a real group's data, so
redaction is not opt-out-able:

| Data | Treatment |
|---|---|
| `sessions` (live bearer tokens) | **never exportable** — no route reaches it; emptied in the snapshot |
| `rooms.invite_token`, `members.pin`, `members.account_number` | replaced with `[redacted]` |
| base64 image payloads in `attachments` | dropped, replaced by `{"omitted":true,"bytes":N}`; pass `keep_images=true` to restore |

Draft attachments (`items`, `adjustments`, `bill_total`) are **preserved** —
they're usually the numbers you're debugging. A NULL `members.pin` stays NULL,
so "unclaimed account" is still distinguishable in a snapshot.

**Two things it deliberately does not do**, so SSH is still the tool for them:
it never writes (no schema changes, no backups, no restarts — §3/§4 stay
SSH-only), and it exposes nothing about the container or host (no `docker ps`,
disk, or build-OOM state). Frontend and Caddy logs are separate containers and
are not served here either.

## 7. Quick health checks

```bash
curl -sS https://chiatienan.duckdns.org/health        # {"status":"ok"} (routed to frontend; cosmetic)
docker compose ps                                     # containers up?
docker compose exec backend python -c "from app.db import get_db; print(get_db())"
```
