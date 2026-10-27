#!/bin/sh
# Launcher for the REA MCP server. stdout is the MCP channel: print nothing
# to it here; diagnostics go to stderr.

REA_VERSION=6.8.0
REA_NODE=/opt/rea/node/bin/node
REA_ENTRY=/opt/rea/node_modules/rea-agents/scripts/rea.mjs

# Succeeds when "$1" (e.g. v24.22.1) satisfies ^22.23.4 || ^24.22.1 || >=26.12.1.
node_ok() {
  v=${1#v}
  major=${v%%.*}
  rest=${v#*.}
  minor=${rest%%.*}
  patch=${rest#*.}
  patch=${patch%%[!0-9]*}
  case $major.$minor.$patch in *[!0-9.]* | .* | *..* | *.) return 1 ;; esac
  case $major in
    22) [ "$minor" -gt 23 ] || { [ "$minor" -eq 23 ] && [ "$patch" -ge 4 ]; } ;;
    24) [ "$minor" -gt 22 ] || { [ "$minor" -eq 22 ] && [ "$patch" -ge 1 ]; } ;;
    *)
      [ "$major" -gt 26 ] && return 0
      [ "$major" -eq 26 ] && { [ "$minor" -gt 12 ] || { [ "$minor" -eq 12 ] && [ "$patch" -ge 1 ]; }; }
      ;;
  esac
}

if [ -x "$REA_NODE" ] && [ -f "$REA_ENTRY" ]; then
  exec "$REA_NODE" "$REA_ENTRY" mcp "$@"
fi

if command -v node >/dev/null 2>&1 && command -v npx >/dev/null 2>&1 \
  && node_ok "$(node --version 2>/dev/null)"; then
  exec npx -y "rea-agents@$REA_VERSION" mcp "$@"
fi

echo "REA is not installed: run .claude/cloud/setup-rea.sh or install Node >=24.22.1." >&2
exit 1
