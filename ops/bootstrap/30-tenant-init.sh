#!/usr/bin/env bash
# Phase 30 — tenant tree init + systemd units + audit-verify timer.
#
# Idempotent.

set -euo pipefail

CORVIN_PREFIX="${CORVIN_PREFIX:-/opt/corvin}"
CORVIN_REPO_DIR="${CORVIN_REPO_DIR:-/opt/corvin-repo}"

GREEN='\033[1;32m'; NC='\033[0m'
ok() { printf "${GREEN}  ✓${NC} %s\n" "$*"; }

# Source .env so CORVIN_TENANT_ID is available.
set -a; . "$CORVIN_PREFIX/.env"; set +a
TENANT_ID="${CORVIN_TENANT_ID:-_default}"

# Tenant tree is created by the container entrypoint on first launch
# (corvin_migrate.py is the sole mkdir owner per ADR-0007). Here we only
# ensure the host-side mount-point exists with correct ownership.
mkdir -p "$CORVIN_PREFIX/home"
chown 1000:1000 "$CORVIN_PREFIX/home"
ok "host mount-point ready (uid 1000)"

# ── systemd units ────────────────────────────────────────────────────────
# corvin-compose.service: lifecycle of the compose stack.
# corvin-audit-verify.{service,timer}: nightly hash-chain verify.
install -m 0644 "$CORVIN_REPO_DIR/ops/systemd/corvin-compose.service" \
                /etc/systemd/system/corvin-compose.service
install -m 0644 "$CORVIN_REPO_DIR/ops/systemd/corvin-audit-verify.service" \
                /etc/systemd/system/corvin-audit-verify.service
install -m 0644 "$CORVIN_REPO_DIR/ops/systemd/corvin-audit-verify.timer" \
                /etc/systemd/system/corvin-audit-verify.timer

# Render the EnvironmentFile path so units pick up /opt/corvin/.env
# without baking the path into the unit-file.
mkdir -p /etc/systemd/system/corvin-compose.service.d
cat > /etc/systemd/system/corvin-compose.service.d/override.conf <<EOF
[Service]
EnvironmentFile=$CORVIN_PREFIX/.env
WorkingDirectory=$CORVIN_PREFIX
EOF

systemctl daemon-reload
systemctl enable corvin-compose.service     >/dev/null
systemctl enable --now corvin-audit-verify.timer >/dev/null
ok "systemd: corvin-compose enabled, audit-verify timer armed"

# ── Bridge settings placeholder files ────────────────────────────────────────
# docker-compose.yml bind-mounts these as :ro into the container. Docker refuses
# to start if the source path does not exist on the host — create minimal
# placeholder JSON files so `docker compose up` works on a fresh install.
# Operators overwrite these with real tokens before enabling the bridges.
BRIDGE_SETTINGS_DIR="${CORVIN_BRIDGE_SETTINGS_DIR:-$CORVIN_PREFIX/bridge-settings}"
mkdir -p "$BRIDGE_SETTINGS_DIR"
for bridge in discord email whatsapp telegram slack; do
    target="$BRIDGE_SETTINGS_DIR/$bridge.json"
    if [ ! -f "$target" ]; then
        printf '{"whitelist":[],"enabled":false,"_placeholder":true}\n' > "$target"
        chmod 640 "$target"
        ok "bridge-settings placeholder: $bridge.json"
    fi
done
