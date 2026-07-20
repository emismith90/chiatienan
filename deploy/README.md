# Deploy runbook — chiatienan

DigitalOcean droplet + Docker Compose (Caddy auto-TLS + FastAPI backend) + SQLite on a volume.
See the design spec §9–§11 for rationale and the full prerequisite list.

## 0. Prerequisites (you provide)

- **Droplet:** Ubuntu 24.04, ≥ 2 GB RAM, this Mac's SSH key added.
- **Domain:** an A-record → droplet IP (e.g. a free `*.duckdns.org`).
- **Secrets:** Cursor API key; an admin password you choose. (Teams/Entra values added later.)

## 1. One-time droplet setup

```bash
ssh root@<DROPLET_IP>
apt-get update && apt-get install -y docker.io docker-compose-plugin git
systemctl enable --now docker
git clone https://github.com/emismith90/chiatienan.git /opt/chiatienan
cd /opt/chiatienan
```

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

If the bridge smoke returns `"ok": true`, B3 is confirmed on this host and we proceed to build
the Teams gateway, tools, and ledger. If it fails, capture the `error` field and
`docker compose logs backend` — that tells us whether the bridge subprocess couldn't launch.

## 5. Later (after IT provides Teams/Entra)

- Add `MICROSOFT_APP_*` to `.env`, redeploy.
- Set the Azure Bot **messaging endpoint** to `https://<CADDY_DOMAIN>/api/messages`.
- Upload the Teams app package to the group (needs IT-enabled sideloading).

## Redeploy (after code changes)

```bash
cd /opt/chiatienan && git pull && docker compose up -d --build
```

## Backup (optional)

```bash
# nightly cron: copy the SQLite file off-box
sqlite3 /opt/chiatienan/data/chiatienan.db ".backup '/opt/chiatienan/data/backup-$(date +\%F).db'"
```
