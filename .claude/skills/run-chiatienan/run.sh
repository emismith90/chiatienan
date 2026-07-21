#!/usr/bin/env bash
# Launch chiatienan locally: FastAPI backend + Next.js frontend, wired together.
#
# - Creates/updates the backend venv and installs deps if needed.
# - Installs frontend deps if node_modules is missing.
# - Picks free ports (the dev machine often already runs things on 3000/3001/8000).
# - Starts both servers in the background, waits until they answer.
# - Creates a demo room and prints the invite URL to open in a browser.
#
# Leaves both servers running after it exits. Stop them with stop.sh.
# Re-running run.sh stops any instance it previously started, then starts fresh.
#
# Env overrides: ADMIN_PASSWORD (default "devpass"), CURSOR_API_KEY (default empty;
# without it @bot replies error out, everything else works).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"      # .claude/skills/run-chiatienan -> repo root
DATA="$ROOT/data"
mkdir -p "$DATA" "$DATA/cursor-agent"

# Load .env if present, exporting each var so the backend inherits it
# (CURSOR_API_KEY, CURSOR_SDK_MODEL, ADMIN_PASSWORD, QR_*, …). The local-only
# paths DATABASE_URL and CURSOR_SDK_WORKSPACE are overridden below to writable
# spots, since .env points them at the container's /data volume.
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

ADMIN_PASSWORD="${ADMIN_PASSWORD:-devpass}"
CURSOR_API_KEY="${CURSOR_API_KEY:-}"

log() { printf '\033[36m» %s\033[0m\n' "$*"; }

# Stop a previously-launched instance (idempotent re-run).
"$HERE/stop.sh" >/dev/null 2>&1 || true

free_port() {  # free_port <start> -> first free TCP port >= start
  local p="$1"
  while lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; do p=$((p + 1)); done
  echo "$p"
}

wait_for() {  # wait_for <url> <label> <tries>
  local url="$1" label="$2" tries="${3:-40}"
  for _ in $(seq 1 "$tries"); do
    if curl -sf "$url" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "ERROR: $label did not come up ($url). See logs in $DATA." >&2
  return 1
}

# --- backend deps ---------------------------------------------------------
if [ ! -x "$ROOT/backend/.venv/bin/uvicorn" ]; then
  log "Setting up backend venv (one-time)"
  ( cd "$ROOT/backend"
    [ -d .venv ] || python3 -m venv .venv
    .venv/bin/pip install -q -e ".[test]" )
fi

# --- frontend deps --------------------------------------------------------
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  log "Installing frontend deps (one-time)"
  ( cd "$ROOT/frontend" && npm install )
fi

# --- ports ----------------------------------------------------------------
BACKEND_PORT="$(free_port 8000)"
FRONTEND_PORT="$(free_port 3000)"

# --- backend --------------------------------------------------------------
log "Starting backend on :$BACKEND_PORT"
DATABASE_URL="sqlite:///$DATA/chiatienan.db" \
CURSOR_SDK_WORKSPACE="$DATA/cursor-agent" \
CURSOR_API_KEY="$CURSOR_API_KEY" \
ADMIN_PASSWORD="$ADMIN_PASSWORD" \
TZ="Asia/Ho_Chi_Minh" \
nohup "$ROOT/backend/.venv/bin/uvicorn" app.main:app \
  --app-dir "$ROOT/backend" --host 127.0.0.1 --port "$BACKEND_PORT" \
  >"$DATA/backend.log" 2>&1 &
echo $! >"$DATA/backend.pid"
wait_for "http://127.0.0.1:$BACKEND_PORT/health" "backend"

# --- frontend -------------------------------------------------------------
# next.config.ts proxies /api/* and /internal/* to BACKEND_ORIGIN in dev.
log "Starting frontend on :$FRONTEND_PORT"
( cd "$ROOT/frontend"
  BACKEND_ORIGIN="http://127.0.0.1:$BACKEND_PORT" \
  nohup ./node_modules/.bin/next dev -p "$FRONTEND_PORT" \
    >"$DATA/frontend.log" 2>&1 &
  echo $! >"$DATA/frontend.pid" )
wait_for "http://127.0.0.1:$FRONTEND_PORT" "frontend"

# --- demo room ------------------------------------------------------------
log "Creating demo room"
ROOM_JSON="$(curl -s -X POST "http://127.0.0.1:$BACKEND_PORT/api/rooms" \
  -H "content-type: application/json" \
  -H "X-Admin-Password: $ADMIN_PASSWORD" \
  -d '{"name":"Lunch (local)"}')"
TOKEN="$(printf '%s' "$ROOM_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["invite_token"])')"

# Don't echo a real secret from .env; only show the dev default in the clear.
ADMIN_SHOWN=$([ "$ADMIN_PASSWORD" = "devpass" ] && echo "devpass (dev default)" || echo "(from .env)")
MODEL_SHOWN="${CURSOR_SDK_MODEL:-composer-2.5 (default)}"

cat <<EOF

────────────────────────────────────────────────────────────
  chiatienan is running
    Frontend : http://localhost:$FRONTEND_PORT
    Backend  : http://127.0.0.1:$BACKEND_PORT   (/health, /api/*)
    Model    : $MODEL_SHOWN
    Admin pw : $ADMIN_SHOWN

  Open the app (join the demo room, pick a nickname + PIN):
    http://localhost:$FRONTEND_PORT/join/$TOKEN

  Logs : $DATA/backend.log   $DATA/frontend.log
  Stop : bash $HERE/stop.sh
$( [ -z "$CURSOR_API_KEY" ] && echo "
  NOTE: CURSOR_API_KEY unset -> @bot mentions will error. Everything
        else (join, chat, drafts, profile, live updates) works." )
────────────────────────────────────────────────────────────
EOF
