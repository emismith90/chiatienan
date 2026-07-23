---
name: deploy-chiatienan
description: Use when deploying, redeploying, or shipping the chiatienan app to production, applying a DB/schema change to prod, OR debugging/inspecting the live app — reading the production chatlog or ledger DB, checking prod logs, or when the bot/balances misbehave in prod. Covers the DigitalOcean droplet, SSH access, Docker Compose, SQLite/WAL, and the PWA service-worker cache.
---

# Deploy & debug chiatienan (production)

chiatienan runs on a **DigitalOcean droplet** at `/opt/chiatienan` via Docker
Compose (Caddy auto-TLS + FastAPI backend + Next.js frontend), SQLite on the
`./data` volume. Domain `chiatienan.duckdns.org` (browser only).

Two reference docs hold the detail — read the one that matches the task:
- **Deploying / redeploying / first-time setup** → [`deploy/README.md`](../../../deploy/README.md) (the runbook: droplet setup, secrets, bring-up, frontend build-OOM handling, redeploy).
- **Debugging / inspecting the live app** → [`deploy/DEBUGGING.md`](../../../deploy/DEBUGGING.md) (logs, reading the chatlog/DB, dump/backup, schema deploy, PWA cache).

## Must not get wrong (read before touching prod)

- **SSH to the domain, not a hardcoded IP:** `ssh -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org` (currently resolves to `165.22.246.208`). The domain follows the droplet if its IP changes — a hardcoded IP goes stale (that's why a dead `143.198.81.194` lingers in `~/.ssh/config`; ignore it). If SSH to the domain lands somewhere unexpected, DuckDNS auto-detect may have grabbed the office IP — **re-pin the DuckDNS record to the droplet IP**, don't switch to hardcoding it.
- **The office network blocks outbound SSH** (banner stripped → "timed out during banner exchange"). Use a **phone hotspot**, or the **DigitalOcean web console** (browser terminal, always works).
- **Schema change on the live DB = `ALTER TABLE … ADD COLUMN`, NEVER `rm` the DB.** SQLAlchemy `create_all()` only builds columns on a *fresh* DB; it will not alter the live one. Wiping erases the group's real ledger. Back up first (see DEBUGGING.md §3).
- **The droplet has no `sqlite3` binary.** To read the DB, `scp` it off and read locally — and copy **both `chiatienan.db` AND `chiatienan.db-wal`** (WAL mode: recent writes live in `-wal`; the main file is stale until checkpoint). The conversation log is the `room_messages` table.
- **PWA service-worker cache:** after a frontend deploy, unregister the service worker + clear caches before concluding a change "didn't work" — the SW serves stale JS chunks.

## Quick reference

```bash
# SSH (use the domain; currently -> 165.22.246.208)
ssh -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org

# On the droplet — logs & redeploy
cd /opt/chiatienan
docker compose logs --tail=200 backend        # bot/tool errors, tracebacks
git pull && docker compose up -d --build       # redeploy (frontend needs build-OOM care — see README §5)

# Read the prod chatlog (from your machine — copy .db AND -wal)
scp -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org:/opt/chiatienan/data/chiatienan.db     ./prod.db
scp -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org:/opt/chiatienan/data/chiatienan.db-wal ./prod.db-wal
sqlite3 ./prod.db "SELECT count(*) FROM room_messages;"

# Additive schema change on live DB — ALWAYS back up first, then ALTER (two steps):
docker compose exec backend python -c "import sqlite3,datetime,os; os.makedirs('/data/backups',exist_ok=True); sqlite3.connect('/data/chiatienan.db').backup(sqlite3.connect(f'/data/backups/backup-{datetime.date.today()}.db'))"
docker compose exec backend python -c "import sqlite3; sqlite3.connect('/data/chiatienan.db').execute('ALTER TABLE <t> ADD COLUMN <col> <type>')"
```

See [`deploy/DEBUGGING.md`](../../../deploy/DEBUGGING.md) for the full chatlog-dump query, the table map, WAL-safe backup, and health checks.
