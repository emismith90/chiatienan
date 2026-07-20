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

## 5. Later (frontend)

The PWA frontend service will be added to `docker-compose.yml` by a later plan; redeploy after
pulling it in the same way as step 3.

## Redeploy (after code changes)

```bash
cd /opt/chiatienan && git pull && docker compose up -d --build
```

## Backup (optional)

```bash
# nightly cron: copy the SQLite file off-box
sqlite3 /opt/chiatienan/data/chiatienan.db ".backup '/opt/chiatienan/data/backup-$(date +\%F).db'"
```
