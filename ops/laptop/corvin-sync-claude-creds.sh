#!/usr/bin/env bash
# Sync ~/.claude/.credentials.json to the Corvin Docker bind-mount.
# Runs every 30 min via corvin-claude-creds-sync.timer.
#
# The local `claude` CLI refreshes the OAuth token transparently when you
# use it (the lifetime is ~8h). The Docker container has no headed-user
# context and cannot refresh on its own — so we mirror the fresh local
# file in. No restart needed; the file is a bind-mount and `claude` in
# the container reads it on every spawn.
set -euo pipefail
SRC="$HOME/.claude/.credentials.json"
DST_HOST="${CORVIN_SYNC_HOST:-root@178.105.220.226}"
DST_TMP="/var/tmp/_claude.json"
DST_REAL="${CORVIN_SYNC_CREDENTIALS_DST:-/opt/corvin/home/.claude/.credentials.json}"
log() { logger -t corvin-creds-sync "$*"; }

[ -f "$SRC" ] || { log "no local credentials.json — skipping"; exit 0; }

# Skip if local is itself already expired (no point shipping a dead token).
if ! python3 -c "
import json, sys, time
d = json.load(open('$SRC'))
exp_s = d['claudeAiOauth']['expiresAt'] / 1000
sys.exit(0 if exp_s > time.time() else 1)
"; then
  log "local credentials are expired — skipping"
  exit 0
fi

# Push via ssh-stdin (avoid the SFTP-broken /tmp scp issue on this host).
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$DST_HOST" \
       "umask 077 && cat > $DST_TMP" < "$SRC"; then
  log "scp-via-stdin to $DST_HOST failed"
  exit 1
fi

ssh -o ConnectTimeout=10 -o BatchMode=yes "$DST_HOST" \
    "sudo install -m 0600 -o 1000 -g 1000 $DST_TMP $DST_REAL && shred -u $DST_TMP" \
  || { log "remote install failed"; exit 1; }

log "ok"
