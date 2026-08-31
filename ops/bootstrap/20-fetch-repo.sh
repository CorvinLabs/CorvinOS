#!/usr/bin/env bash
# Phase 20 — pin the repo checkout to the latest semver tag and copy the
# compose/caddy templates into ${CORVIN_PREFIX}.
#
# Tag-pinning matches the autoupdate convention (CLAUDE.md): production
# deployments track v* tags, never branch-HEAD.

set -euo pipefail

CORVIN_PREFIX="${CORVIN_PREFIX:-/opt/corvin}"
CORVIN_REPO_DIR="${CORVIN_REPO_DIR:-/opt/corvin-repo}"

GREEN='\033[1;32m'; NC='\033[0m'
ok() { printf "${GREEN}  ✓${NC} %s\n" "$*"; }

cd "$CORVIN_REPO_DIR"
git fetch --tags --quiet

# Pin to the highest semver tag if any exist; else stay on branch.
latest_tag=$(git tag -l 'v*' --sort=-v:refname | head -n1 || true)
if [[ -n "$latest_tag" ]]; then
    git checkout --quiet "$latest_tag"
    ok "checkout pinned to tag $latest_tag"
else
    ok "no v* tags found — staying on $(git rev-parse --abbrev-ref HEAD)"
fi

# Copy compose + caddy templates to ${CORVIN_PREFIX}. Keep them out of
# the repo working tree so 'git pull' doesn't fight with operator edits.
install -m 0644 ops/docker-compose.yml   "$CORVIN_PREFIX/docker-compose.yml"
install -m 0644 ops/Caddyfile.template   "$CORVIN_PREFIX/Caddyfile"

# Materialize .env only if it doesn't exist — never clobber operator edits.
if [[ ! -f "$CORVIN_PREFIX/.env" ]]; then
    install -m 0600 ops/.env.template "$CORVIN_PREFIX/.env"
    ok "seeded $CORVIN_PREFIX/.env from template"
else
    ok "$CORVIN_PREFIX/.env exists — left untouched"
fi
