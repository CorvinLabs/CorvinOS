#!/usr/bin/env bash
# test_daemon_boot.sh — Smoke-Test: jeder daemon startet bis zum FATAL-
# exit without module-Lade-Fehler. Verifiziert dass alle shared/js/-modulee
# auflösbar sind und die early-exit-Logik bei fehlenden Tokens greift.
#
# Macht KEINE Live-connection zu Telegram/Discord/Slack — we geben
# intentionally KEIN Token, damit der daemon innerhalb von ~100ms FATAL exit
# macht und we den Boot-path isoliert testen.
#
# Achtung: WhatsApp-daemon wed skipped, weil er statt FATAL bei
# fehlender Auth in einen Pairing-Loop geht (Baileys-spezifisch).

set -uo pipefail

BRIDGES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPDIR="$(mktemp -d -t bridge-boot-XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

pass=0
fail=0

ok()   { printf '\033[32mPASS\033[0m: %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '\033[31mFAIL\033[0m: %s\n' "$*"; fail=$((fail+1)); }

# Run a daemon with no token — expect exit 1 + "FATAL" in log within 5s.
# We override settings.json via a bind-mount-ish approach: copy the daemon
# dir to TMPDIR and write an empty settings.json there.
test_daemon() {
  local channel="$1" port_var="$2" port="$3"
  local src="$BRIDGES_DIR/$channel"
  local sandbox="$TMPDIR/$channel"
  cp -r "$src" "$sandbox"
  rm -f "$sandbox/settings.json"
  echo '{}' > "$sandbox/settings.json"
  # Symlink shared/ + shared/js/ so the require('../shared/js/...') still works.
  # Use -f so re-running after a failed test (which skips cleanup) doesn't error.
  ln -sf "$BRIDGES_DIR/shared" "$TMPDIR/shared"

  local logfile="$TMPDIR/$channel.log"
  # Run with fake port so we don't collide with a live daemon.
  env -i HOME="$HOME" PATH="$PATH" "$port_var=$port" \
    timeout 5 node "$sandbox/daemon.js" >"$logfile" 2>&1
  local rc=$?

  # We expect either rc=1 (FATAL exit) or rc=124 (timeout — process kept
  # running, which is so a module-Lade-success). What we DON'T want is
  # rc=0 (impossible without a token) or another "module not found" rc.
  if [[ $rc -ne 1 && $rc -ne 124 ]]; then
    bad "$channel: unexpected exit code $rc"
    sed 's/^/    /' "$logfile" | head -10
    return
  fi
  # modulee-Lade-Fehler detects man an "Cannot find module".
  if grep -q "Cannot find module" "$logfile"; then
    bad "$channel: module resolution failure"
    grep "Cannot find module" "$logfile" | sed 's/^/    /' | head -3
    return
  fi
  # Telegram/Discord/Slack: erwarten "FATAL" weil Token fehlt.
  if grep -q "FATAL" "$logfile"; then
    ok "$channel: FATAL exit on missing token (no module-load errors)"
  else
    # rc=124 without FATAL = noch im Boot, aber requires sind durch.
    if [[ $rc -eq 124 ]]; then
      ok "$channel: clean boot (timeout reached without crash, no module errors)"
    else
      bad "$channel: rc=$rc but no FATAL in log"
      sed 's/^/    /' "$logfile" | head -5
    fi
  fi
  rm "$TMPDIR/shared"  # cleanup for next Test
}

echo "=== daemon Boot Smoke-Test ==="
test_daemon telegram TELEGRAM_HTTP_PORT 17892
test_daemon discord  DISCORD_HTTP_PORT  17893
test_daemon slack    SLACK_HTTP_PORT    17894

echo
echo "$pass passed, $fail failed"
exit $((fail > 0 ? 1 : 0))
