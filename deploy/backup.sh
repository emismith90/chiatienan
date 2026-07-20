#!/usr/bin/env sh
# Nightly SQLite backup (design §9). Uses `sqlite3 .backup` for a consistent copy
# even while the app is writing (WAL-safe). Schedule from the droplet host, e.g.
#
#   0 3 * * *  /path/to/deploy/backup.sh >> /var/log/chiatienan-backup.log 2>&1
#
# Optionally push $DEST to DO Spaces with `s3cmd`/`rclone` afterwards.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/data/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR}/chiatienan-${STAMP}.db"

mkdir -p "$BACKUP_DIR"

# Run inside the backend container so the path + sqlite3 match the live DB.
docker compose -f "$(dirname "$0")/docker-compose.yml" exec -T backend \
	sh -c "apt-get -qq install -y sqlite3 >/dev/null 2>&1 || true; \
	       sqlite3 /data/chiatienan.db \".backup '/data/backups/chiatienan-${STAMP}.db'\""

echo "backup written: ${DEST}"

# Keep the 14 most recent backups.
ls -1t "${BACKUP_DIR}"/chiatienan-*.db 2>/dev/null | tail -n +15 | xargs -r rm -f
