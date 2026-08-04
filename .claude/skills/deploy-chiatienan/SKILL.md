---
name: deploy-chiatienan
description: Use when deploying, redeploying, or shipping the chiatienan app to production, applying a DB/schema change to prod, OR debugging/inspecting the live app — reading the production chatlog/conversation log or ledger DB, exporting prod data as CSV, checking prod logs, or when the bot/balances misbehave in prod. Covers the HTTPS debug/export API (works without SSH), the DigitalOcean droplet, SSH access, Docker Compose, SQLite/WAL, and the PWA service-worker cache.
---

# Deploy & debug chiatienan (production)

chiatienan runs on a **DigitalOcean droplet** at `/opt/chiatienan` via Docker
Compose (Caddy auto-TLS + FastAPI backend + Next.js frontend), SQLite on the
`./data` volume. Domain `chiatienan.duckdns.org` (browser only).

Two reference docs hold the detail — read the one that matches the task:
- **Deploying / redeploying / first-time setup** → [`deploy/README.md`](../../../deploy/README.md) (the runbook: droplet setup, secrets, bring-up, frontend build-OOM handling, redeploy).
- **Debugging / inspecting the live app** → [`deploy/DEBUGGING.md`](../../../deploy/DEBUGGING.md) (the export API in §6, logs, reading the chatlog/DB, dump/backup, schema deploy, PWA cache).

## Reading prod data: try the export API first

**To *read* anything from prod — the conversation log, the ledger, the app log —
use the HTTPS export API, not SSH.** It needs no SSH key material and no port 22
— just `DEBUG_API_KEY` in a header — so it works from a restricted cloud agent.
SSH is for *changing* things.

```bash
export H="X-Debug-Key: $DEBUG_API_KEY"          # from the droplet's .env
export B=https://chiatienan.duckdns.org/internal/debug

curl -sS -H "$H" $B/ping                                     # key OK? row counts
curl -sS -H "$H" $B/rooms                                    # find the real room_id
curl -sS -H "$H" "$B/conversation.txt?room_id=1&days=14"     # readable transcript
curl -sS -H "$H" "$B/conversation.csv?room_id=1" -o chatlog.csv
curl -sS -H "$H" "$B/tables/meals.csv?room_id=1" -o meals.csv
curl -sS -H "$H" $B/db -o prod-snapshot.db                   # sanitised, WAL-safe, ONE file
curl -sS -H "$H" "$B/logs?lines=300"                         # backend + uvicorn tracebacks
```

- **404 on every route** has *two* causes, and they need different fixes:
  1. `DEBUG_API_KEY` is unset (or under 24 chars) on the droplet → API deliberately disabled. The intended "off" state, not a bug.
  2. **The running image predates the debug API** — the routes genuinely do not exist.

  Tell them apart with a method mismatch, since routing resolves before the key gate:
  ```bash
  curl -sS -o /dev/null -w '%{http_code}\n' -X POST $B/ping   # 405 = route exists (key off) | 404 = code not deployed
  curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://chiatienan.duckdns.org/api/me   # control: must be 405
  ```
  The 404 *body* corroborates it: `{"detail":"not found"}` (lowercase) is the key gate; `{"detail":"Not Found"}` is Starlette saying no such route. **401** = the key is wrong.
- **Where the key comes from:** the droplet's `/opt/chiatienan/.env`. It is a `secrets.token_urlsafe(32)` value (43 chars), so it survives a URL/header unencoded — if you pass it around base64'd, remember that is an *encoding, not encryption*: never commit either form to the repo, a skill file, or a PR. Rotate by changing `.env` and restarting the backend; no other component stores it.
- **Secrets never come back.** `sessions` (live bearer tokens) is unexportable; `invite_token`, `pin`, `account_number` are always `[redacted]`; base64 image payloads are stripped (`keep_images=true` restores them). Draft `items`/`adjustments`/`bill_total` *are* preserved — usually the numbers you're debugging.
- **`$B/db` needs no `-wal` sidecar** — it checkpoints before serving, unlike `scp`.
- **Still SSH-only:** anything that writes (schema `ALTER`, backups, restart, redeploy) and anything about the container/host (`docker ps`, disk, build-OOM). Frontend and Caddy logs are separate containers and not served by the API.

## Must not get wrong (read before touching prod)

- **SSH to the domain, not a hardcoded IP:** `ssh -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org` (currently resolves to `165.22.246.208`). The domain follows the droplet if its IP changes — a hardcoded IP goes stale (that's why a dead `143.198.81.194` lingers in `~/.ssh/config`; ignore it). If SSH to the domain lands somewhere unexpected, DuckDNS auto-detect may have grabbed the office IP — **re-pin the DuckDNS record to the droplet IP**, don't switch to hardcoding it.
- **The office network blocks outbound SSH** (banner stripped → "timed out during banner exchange"). Use a **phone hotspot**, or the **DigitalOcean web console** (browser terminal, always works). A **sandboxed cloud agent** may be worse: egress restricted to :443 through a filtering proxy, so port 22 times out to the domain *and* the raw IP, and even `CONNECT …:22` through the proxy is refused — no key fixes that. Confirm with `curl -sS "$HTTPS_PROXY/__agentproxy/status"` and read prod through the export API instead. Egress is per-host: once `chiatienan.duckdns.org:443` is allowlisted the export API works fully while **port 22 stays shut** (and the raw IP may still 403 — always use the domain). A cloud agent therefore can *read and diagnose* prod, but cannot deploy it; dispatching the Deploy workflow also needs `actions: write`, which the GitHub app token does not carry.
- **Using `DEPLOY_SSH_KEY` from a cloud session.** The cloud environment can carry the deploy key as a base64 `.env` variable (Settings → *Update cloud environment* → *Environment variables*; changes apply to **new sessions only**). To use it:
  ```bash
  command -v ssh || apt-get install -y -q openssh-client   # the image ships without it
  install -d -m 700 ~/.ssh
  printf '%s' "$DEPLOY_SSH_KEY" | base64 -d > ~/.ssh/id_deploy
  chmod 600 ~/.ssh/id_deploy                                # openssh refuses group/world-readable keys
  ssh -i ~/.ssh/id_deploy -o IdentitiesOnly=yes root@chiatienan.duckdns.org \
    'cd /opt/chiatienan && docker compose ps'
  ```
  Same key and path the runner uses in `deploy.yml`. `IdentitiesOnly=yes` stops ssh offering other identities first.
- **If that ssh hangs, it is the network policy, not the key.** Allowlisting a domain opens **:443 only** — port 22 stays shut, and `CONNECT …:22` through the proxy is refused (verified: `connect to host … port 22: Connection timed out` while `:443` served the export API fine). Distinguish the two: a timeout with no banner is egress; `Permission denied (publickey)` means the path is open and the key or user is wrong. Opening :22 requires changing the environment's network access, not adding the key.
- **Treat that variable as a root credential.** The env-var panel warns its contents are "visible to anyone using this environment", and this key is root on the droplet. Prefer the CI runner (which already holds it as a repo secret with unrestricted egress) and the DigitalOcean web console for hands-on work; if a session key is stored anyway, keep it to a dedicated keypair you can revoke from `authorized_keys` without touching the deploy pipeline.
- **Deploys are CI, not `git pull` on the droplet.** `.github/workflows/deploy.yml` builds images on GitHub, pushes them to GHCR, and the droplet only *pulls* (`docker compose pull && up -d`, `IMAGE_TAG` pinned to the commit SHA) — deliberately, so the 512 MB host never runs `next build`. Trigger it by merging to `main` or via **Actions → Deploy → Run workflow**. Do not run `up -d --build` on the droplet.
- **A green Deploy run used to not mean the new code is live** — and the failure mode is worth knowing even now that it is fixed. The droplet filled up, `docker compose pull` died with `no space left on device`, `up -d` silently kept the previous containers, and the run went green on stale code twice in one morning (prod sat on `3a7799c2` while `main` was `a127ec8`). It was masked because the remote command ended `… || true; docker compose ps`, making `ps` the step's exit status. `deploy.yml` now prunes `-af` **before** the pull, `;`-separates every remote step under `set -e`, and ends with a **Verify the running images match this commit** step that fails the job when the running tag ≠ `github.sha`. Still worth spot-checking by hand:
  ```bash
  docker compose ps --format '{{.Service}} {{.Image}}'   # must match the deployed commit SHA
  df -h /var/lib/docker && docker image prune -af        # the usual cause, and the fix
  ```
- **`.env` on the droplet is regenerated from GitHub secrets on every deploy** ("Provision .env from secrets" overwrites it wholesale; the previous file is kept as `.env.bak.<timestamp>`). Anything missing from that heredoc is *erased on the next deploy* — which is exactly how `DEBUG_API_KEY` kept vanishing. `DEBUG_API_KEY`, `LOG_FILE`, `LOG_MAX_BYTES` and `LOG_BACKUP_COUNT` are now in the heredoc, so hand-editing the droplet is no longer needed — but `DEBUG_API_KEY` has to exist as a repo **Actions secret** (`Settings → Secrets and variables → Actions`), or it is written empty and the export API stays disabled. The run logs a `::warning::` when that happens.
- **Additive columns are automatic now; anything else is hand-written, and NEVER `rm` the DB.** `app.db.create_all()` adds missing tables *and* `ALTER TABLE … ADD COLUMN`s any column the models gained (idempotent), so a new `mapped_column` just needs a deploy. Dropping/renaming/retyping a column still needs a manual migration — back up first (DEBUGGING.md §3). Wiping erases the group's real ledger.
- **"All the data got wiped" is usually a missing column, not data loss.** Plain `create_all()` skips existing tables, so before the auto-migration a new column left every SELECT on that model raising `no such column` and the room rendered blank while every row sat safe on disk (this was PR #34 / `members.default_participant`). Check `$B/logs | grep "no such column"` and `$B/ping` row counts before believing anything is gone.
- **The droplet has no `sqlite3` binary.** Prefer `$B/db` (above). If you do `scp`, copy **both `chiatienan.db` AND `chiatienan.db-wal`** (WAL mode: recent writes live in `-wal`; the main file is stale until checkpoint). The conversation log is the `room_messages` table.
- **WAL bites any copy, not just `scp`.** A `.backup()` copy inherits `journal_mode=WAL`, so later writes to that copy land in *its* `-wal` — modify a snapshot and ship only the main file and your changes silently vanish. Checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)` + `journal_mode=DELETE`) before treating a snapshot as one self-contained file.
- **PWA service-worker cache:** after a frontend deploy, unregister the service worker + clear caches before concluding a change "didn't work" — the SW serves stale JS chunks.

## Quick reference

```bash
# SSH (use the domain; currently -> 165.22.246.208)
ssh -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org

# On the droplet — logs & deploy verification (deploys themselves run in CI)
cd /opt/chiatienan
docker compose logs --tail=200 backend           # bot/tool errors, tracebacks
docker compose ps --format '{{.Service}} {{.Image}}'   # which SHA is ACTUALLY running
df -h /var/lib/docker                            # full disk => silent stale-image deploys
docker image prune -af && docker builder prune -af     # reclaim, then re-run the Deploy workflow

# Read the prod chatlog — prefer the export API (no SSH; see section above)
curl -sS -H "X-Debug-Key: $DEBUG_API_KEY" \
  "https://chiatienan.duckdns.org/internal/debug/conversation.txt?room_id=1&days=14"

# SSH fallback, if the API is disabled (copy .db AND -wal)
scp -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org:/opt/chiatienan/data/chiatienan.db     ./prod.db
scp -i ~/.ssh/digitalocean-openclaw root@chiatienan.duckdns.org:/opt/chiatienan/data/chiatienan.db-wal ./prod.db-wal
sqlite3 ./prod.db "SELECT count(*) FROM room_messages;"

# Additive schema change on live DB — ALWAYS back up first, then ALTER (two steps):
docker compose exec backend python -c "import sqlite3,datetime,os; os.makedirs('/data/backups',exist_ok=True); sqlite3.connect('/data/chiatienan.db').backup(sqlite3.connect(f'/data/backups/backup-{datetime.date.today()}.db'))"
docker compose exec backend python -c "import sqlite3; sqlite3.connect('/data/chiatienan.db').execute('ALTER TABLE <t> ADD COLUMN <col> <type>')"
```

See [`deploy/DEBUGGING.md`](../../../deploy/DEBUGGING.md) for the full chatlog-dump query, the table map, WAL-safe backup, and health checks.
