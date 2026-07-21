#!/usr/bin/env bash
# Stop the backend + frontend started by run.sh (by pidfile).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
DATA="$ROOT/data"

for name in frontend backend; do
  pidfile="$DATA/$name.pid"
  [ -f "$pidfile" ] || continue
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null && echo "stopped $name (pid $pid)"
  fi
  rm -f "$pidfile"
done
