#!/usr/bin/env bash
# Corvin container entrypoint
#
# Runs as root, then drops to the corvin user via supervisord's `user=`
# directive. Responsibilities:
#   1. Validate that bind-mounted home is writable by uid 1000.
#   2. Initialize the tenant tree if missing (idempotent).
#   3. Hand off to whatever was passed as CMD (default: supervisord).

set -euo pipefail

HOME_DIR="/home/corvin"
TENANT_ID="${CORVIN_TENANT_ID:-_default}"

log() { printf '[entrypoint] %s\n' "$*" >&2; }

# Ensure the bind-mounted home is accessible as uid 1000. If the host
# bind-mount has different ownership, fix it once. This is the only place
# we chown — operator's secret-injection later writes as uid 1000 directly.
if [[ -d "$HOME_DIR" ]]; then
    current_uid=$(stat -c %u "$HOME_DIR")
    if [[ "$current_uid" != "1000" ]]; then
        log "fixing ownership of $HOME_DIR (was uid=$current_uid, want 1000)"
        chown -R 1000:1000 "$HOME_DIR"
    fi
fi

# Tenant tree init — idempotent. The full migrate helper exists only as a
# library; for non-default tenants we materialize the canonical subtree directly.
TENANT_ROOT="$HOME_DIR/.corvin/tenants/$TENANT_ID"
if [[ ! -d "$TENANT_ROOT" ]]; then
    log "creating tenant tree for $TENANT_ID at $TENANT_ROOT"
    install -d -o 1000 -g 1000 -m 0755 \
        "$TENANT_ROOT" \
        "$TENANT_ROOT/global" \
        "$TENANT_ROOT/sessions" \
        "$TENANT_ROOT/forge" \
        "$TENANT_ROOT/skill-forge" \
        "$TENANT_ROOT/voice" \
        "$TENANT_ROOT/cowork" \
        "$HOME_DIR/.corvin/run" \
        "$HOME_DIR/.corvin/logs"

    # Backward-compat symlinks per ADR-0007 (only for _default tenant).
    if [[ "$TENANT_ID" == "_default" ]]; then
        for sub in global sessions forge skill-forge voice cowork; do
            ln -snf "tenants/_default/$sub" "$HOME_DIR/.corvin/$sub"
        done
    fi
fi

# Hand off
log "starting: $*"
exec "$@"
