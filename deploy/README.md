# Deploy runbook — chiatienan

DigitalOcean droplet + Docker Compose (Caddy auto-TLS + FastAPI backend) + SQLite on a volume.
See the design spec §9–§11 for rationale and the full prerequisite list.

## 0. Prerequisites (you provide)

- **Droplet:** Ubuntu 24.04, ≥ 2 GB RAM, this Mac's SSH key added.
- **Domain:** an A-record → droplet IP (e.g. a free `*.duckdns.org`).
- **Secrets:** Cursor API key; an admin password you choose.

## 1. One-time droplet setup

```bash
ssh root@<DROPLET_IP>
apt-get update && apt-get install -y git curl
curl -fsSL https://get.docker.com | sh        # docker-ce + compose plugin (Ubuntu repos lack docker-compose-plugin)
systemctl enable --now docker
git clone https://github.com/emismith90/chiatienan.git /opt/chiatienan
cd /opt/chiatienan
```

> **Low-RAM droplet?** On a 1 GB (or smaller) droplet, add swap first so the build and the
> bridge subprocess don't OOM:
> ```bash
> fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
> echo '/swapfile none swap sw 0 0' >> /etc/fstab
> ```
> ≥ 2 GB RAM is still recommended (see spec §9).

## 2. Configure secrets (you do this — secrets never go in chat or git)

```bash
cp .env.example .env
nano .env          # set CADDY_DOMAIN, CURSOR_API_KEY, ADMIN_PASSWORD
```

## 3. Bring it up

```bash
cd /opt/chiatienan
docker compose up -d --build
docker compose logs -f caddy      # watch the TLS cert get issued
```

## 4. Validate the pipeline

```bash
# Health (through Caddy + TLS):
curl https://<CADDY_DOMAIN>/health
# -> {"status":"ok"}

# B3 — does the Cursor SDK bridge actually run in the container?
curl -X POST https://<CADDY_DOMAIN>/internal/bridge-smoke \
     -H "X-Admin-Password: <ADMIN_PASSWORD>"
# -> {"ok": true, "elapsed_s": ..., "messages_seen": >0, "text": "...pong..."}
```

If the bridge smoke returns `"ok": true`, the Cursor SDK bridge is confirmed working on this
host. If it fails, capture the `error` field and `docker compose logs backend` — that tells you
whether the bridge subprocess couldn't launch.

## 5. Frontend (PWA)

`docker-compose.yml` now has a `frontend` service (Next.js, `output: "standalone"`, port 3000
internal-only) and `Caddyfile` routes `/api/*` + `/internal/*` to `backend:8000`, everything else
to `frontend:3000` (Caddy streams the SSE route unbuffered by default — no extra config needed).

> **M8 — do not build the frontend image on a tiny droplet.** `next build` OOMs on a 512 MB
> droplet; a plain `docker compose up -d --build` there can hang or get OOM-killed. Pick one:
>
> **Option A — build on-box, but only with the 2 GB swap from step 1 already enabled.**
> ```bash
> free -h   # confirm the 2G swapfile from step 1 is active before building
> cd /opt/chiatienan && git pull && docker compose up -d --build
> ```
>
> **Option B — build elsewhere and transfer the image (no swap needed on the droplet).**
> ```bash
> # on a machine with >= 2 GB RAM (a laptop or CI runner), from a checkout of this repo:
> docker compose build frontend
> docker save chiatienan-frontend | ssh root@<DROPLET_IP> 'docker load'
> # then, on the droplet: rebuild only backend, and bring everything up without rebuilding
> # frontend (compose reuses the just-loaded image since it's already tagged correctly):
> ssh root@<DROPLET_IP> 'cd /opt/chiatienan && git pull && docker compose build backend && docker compose up -d'
> ```
> (`chiatienan-frontend` is the image name Compose derives from the project dir + service name —
> confirm with `docker compose images frontend` if the project folder isn't named `chiatienan` on
> both machines.)

After either option, validate:

```bash
curl -I https://<CADDY_DOMAIN>/          # -> 200 from the Next.js frontend
curl https://<CADDY_DOMAIN>/health       # -> {"status":"ok"} from the backend, still routed correctly
```

Then do the phone/PWA walkthrough: open `https://<CADDY_DOMAIN>` on a phone, install the PWA, open
an admin-created invite link, create an account, send `@bot ghi 100k, an và binh`, confirm the bot
reply appears for all members.

## Redeploy (after code changes)

### Automated (GitHub Actions — preferred)

Merging to `main` runs `.github/workflows/deploy.yml`: it builds both images **on the GitHub
runner**, pushes them to GHCR, then SSHes in to regenerate `.env` and `docker compose pull &&
up -d`. Because the image is built off-box, this side-steps M8 entirely — the droplet never runs
`next build`. See the workflow header for the required repo secrets/variables
(`DEPLOY_SSH_KEY`, `DEPLOY_HOST`, `CURSOR_API_KEY`, `ADMIN_PASSWORD`, `CADDY_DOMAIN`, …).

`.env` is regenerated from GitHub secrets on each deploy (the previous file is backed up to
`.env.bak.<timestamp>`), so GitHub is the source of truth for production config. If the app
secrets aren't set, the deploy leaves the existing `.env` untouched.

### Manual (fallback)

```bash
cd /opt/chiatienan && git pull && docker compose up -d --build
```

Backend-only or Caddy-only changes: the command above is fine as-is. If the change touches
`frontend/`, do **not** run a blanket `--build` on the droplet — see the M8 note in
section 5 and use Option A (with swap) or Option B (build-elsewhere + `docker load`) instead.
To pull the CI-built images by hand instead of building: `docker login ghcr.io` (with a
`read:packages` token), then `docker compose pull && docker compose up -d`.

## Backup (optional)

```bash
# nightly cron: copy the SQLite file off-box
sqlite3 /opt/chiatienan/data/chiatienan.db ".backup '/opt/chiatienan/data/backup-$(date +\%F).db'"
```
