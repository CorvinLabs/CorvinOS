#!/usr/bin/env bash
# Watch the console frontend sources and redeploy on every change.
#
# Deliberately dependency-free: inotify-tools is not installed on this host and
# Node's recursive fs.watch needs a newer runtime than /usr/bin/node provides.
# A cheap mtime fingerprint over the source tree is plenty at this scale — the
# scan costs milliseconds next to a ~30s build.
#
# Each change runs the same scripts/console-deploy.sh the operator would run by
# hand, so what the watcher deploys is verified over the wire exactly the same
# way — including the type-check, which `vite build --watch` alone would skip.
#
# Usage: scripts/console-watch.sh [--interval SECONDS]
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_NEXT="$REPO_ROOT/core/console/corvin_console/web-next"
INTERVAL="${CORVIN_CONSOLE_WATCH_INTERVAL:-2}"

while [ $# -gt 0 ]; do
  case "$1" in
    --interval) INTERVAL="${2:-2}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

fingerprint() {
  # dist/ and node_modules/ are OUTPUTS — including them would make every build
  # trigger the next one forever.
  find "$WEB_NEXT/src" "$WEB_NEXT/index.html" \
       "$WEB_NEXT/vite.config.ts" "$WEB_NEXT/tailwind.config.ts" \
       "$WEB_NEXT/package.json" \
       -type f -printf '%T@ %s %p\n' 2>/dev/null | sort | cksum
}

log() { printf '%s console-watch: %s\n' "$(date '+%H:%M:%S')" "$*"; }

log "watching $WEB_NEXT/src (every ${INTERVAL}s)"
LAST="$(fingerprint)"

# Deploy once at start: sources may have moved while the watcher was down.
if OUT="$("$REPO_ROOT/scripts/console-deploy.sh" 2>&1)"; then
  log "initial deploy — ${OUT##*$'\n'}"
else
  log "initial deploy FAILED — ${OUT##*$'\n'}"
fi

while true; do
  sleep "$INTERVAL"
  NOW="$(fingerprint)"
  [ "$NOW" = "$LAST" ] && continue

  # Settle: an editor writing several files (or a multi-file edit) should be one
  # build, not one per file.
  while true; do
    sleep "$INTERVAL"
    SETTLED="$(fingerprint)"
    [ "$SETTLED" = "$NOW" ] && break
    NOW="$SETTLED"
  done

  LAST="$NOW"
  log "change detected — rebuilding"
  if OUT="$("$REPO_ROOT/scripts/console-deploy.sh" --fast 2>&1)"; then
    log "${OUT##*$'\n'}"
  else
    log "FAILED — ${OUT##*$'\n'}"
  fi
  # Source may have changed again during the build; re-baseline so the next
  # loop compares against post-build reality.
  LAST="$(fingerprint)"
done
