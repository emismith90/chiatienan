#!/usr/bin/env bash
# Installs Node 24 and rea-agents under /opt/rea for the REA MCP server.
# Self-contained: paste into the cloud environment's "Setup script" field.
set -euo pipefail

NODE_VERSION=24.22.1
NODE_SHA256=6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6
REA_VERSION=6.8.0
REA_PREFIX=/opt/rea

NODE_BIN="$REA_PREFIX/node/bin"
REA_PKG="$REA_PREFIX/node_modules/rea-agents"

installed_node=$("$NODE_BIN/node" --version 2>/dev/null || true)
installed_rea=$("$NODE_BIN/node" -p "require('$REA_PKG/package.json').version" 2>/dev/null || true)
if [ "$installed_node" = "v$NODE_VERSION" ] && [ "$installed_rea" = "$REA_VERSION" ]; then
  echo "REA $REA_VERSION already installed with Node v$NODE_VERSION in $REA_PREFIX; nothing to do."
  exit 0
fi

mkdir -p "$REA_PREFIX"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

tarball="node-v$NODE_VERSION-linux-x64.tar.xz"
echo "Downloading Node v$NODE_VERSION..."
curl --fail --show-error --silent --location --retry 3 \
  --output "$tmp/$tarball" "https://nodejs.org/dist/v$NODE_VERSION/$tarball"
(cd "$tmp" && echo "$NODE_SHA256  $tarball" | sha256sum -c -)

# Extract next to the destination so the final mv is a same-filesystem rename.
stage=$(mktemp -d "$REA_PREFIX/.node-stage.XXXXXX")
trap 'rm -rf "$tmp" "$stage"' EXIT
tar -xJf "$tmp/$tarball" -C "$stage" --strip-components=1
rm -rf "$REA_PREFIX/node"
mv "$stage" "$REA_PREFIX/node"

echo "Installing rea-agents@$REA_VERSION..."
PATH="$NODE_BIN:$PATH" "$NODE_BIN/npm" install --prefix "$REA_PREFIX" --no-audit --no-fund "rea-agents@$REA_VERSION"

reported=$("$NODE_BIN/node" "$REA_PKG/scripts/rea.mjs" --version)
if ! printf '%s\n' "$reported" | grep -qF "$REA_VERSION"; then
  echo "REA version check failed: expected $REA_VERSION, got: $reported" >&2
  exit 1
fi
echo "REA $reported installed in $REA_PREFIX."
