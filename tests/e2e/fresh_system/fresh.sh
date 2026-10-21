#!/bin/bash
# fresh.sh — a FRESH SYSTEM for CorvinOS, reproducibly: a bare Ubuntu 24.04 with
# systemd (user session via linger), one ordinary user and ONLY git + curl — what
# the README promises is enough. Nothing from the dev machine is inside.
#
#   tests/e2e/fresh_system/fresh.sh build                 # the base image (once)
#   tests/e2e/fresh_system/fresh.sh up NAME               # start a bare system
#   tests/e2e/fresh_system/fresh.sh install NAME [REF]    # git clone + ./install.sh --lan inside (REF: branch/sha, default main)
#   tests/e2e/fresh_system/fresh.sh ex NAME 'cmd…'        # run cmd as the user, with a working systemctl --user
#   tests/e2e/fresh_system/fresh.sh login NAME            # hand the system a Claude Code login (access token only)
#   tests/e2e/fresh_system/fresh.sh rm NAME…              # tear down
#
# Containers share the network "corvinnet" (NAME resolves inside it), so two of
# them can pair over A2A. The console binds 127.0.0.1 inside the container —
# drive it from inside (ex), not from the host.
#
# `login` copies the host's ~/.claude/.credentials.json WITHOUT the refresh
# token: the container can call the model with the operator's own login but can
# never rotate it (a rotated refresh token would log the host out). When the
# access token expires (~8 h), run login again.
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG="${CORVIN_FRESH_IMAGE:-corvin-fresh:base}"
NET="${CORVIN_FRESH_NET:-corvinnet}"
REPO_URL="${CORVIN_FRESH_REPO:-https://github.com/CorvinLabs/CorvinOS.git}"

_ex() {  # _ex NAME cmd…
    local n="$1"; shift
    docker exec -u tester -w /home/tester \
      -e HOME=/home/tester -e USER=tester -e LANG=en_US.UTF-8 \
      -e XDG_RUNTIME_DIR=/run/user/1000 -e DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
      -e PATH=/home/tester/.local/bin:/home/tester/CorvinOS/.corvin/node/bin:/home/tester/.corvin/node/bin:/usr/local/bin:/usr/bin:/bin \
      "$n" bash -lc "$*"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  build)
    docker build -q -t "$IMG" "$HERE/docker" ;;
  up)
    n="$1"
    docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
    docker rm -f "$n" >/dev/null 2>&1 || true
    docker run -d --name "$n" --hostname "$n" --network "$NET" \
      --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
      --tmpfs /run --tmpfs /run/lock --tmpfs /tmp:exec "$IMG" >/dev/null
    for _ in $(seq 1 60); do docker exec "$n" test -S /run/user/1000/bus 2>/dev/null && break; sleep 1; done
    docker exec "$n" test -S /run/user/1000/bus || { echo "user session did not come up in $n" >&2; exit 1; }
    echo "$n up" ;;
  install)
    n="$1"; ref="${2:-main}"
    # The install's own output is kept INSIDE the system as ~/install.log:
    # test_fresh_install_live.py reads it there when CORVIN_FRESH_INSTALL_LOG
    # is not given (the /tmp/corvinos-install.log the installer writes holds
    # only the awaited sub-steps, never corvin-install's step lines).
    _ex "$n" "set -u -o pipefail; cd ~; { echo \"== \$(date -u +%FT%TZ) clone $ref\"; git clone --depth 1 --branch '$ref' '$REPO_URL' CorvinOS 2>&1 | tail -1; cd CorvinOS; git log --oneline -1; echo \"== \$(date -u +%FT%TZ) install.sh --lan\"; ./install.sh --lan; rc=\$?; echo \"== \$(date -u +%FT%TZ) install.sh exit=\$rc\"; exit \$rc; } 2>&1 | tee ~/install.log; exit \${PIPESTATUS[0]}" ;;
  ex)
    n="$1"; shift; _ex "$n" "$@" ;;
  login)
    n="$1"
    tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
    python3 - "$tmp" <<'PY'
import json, os, sys, time
d = json.load(open(os.path.expanduser("~/.claude/.credentials.json")))
o = d["claudeAiOauth"]; o.pop("refreshToken", None); o["refreshTokenExpiresAt"] = 0
json.dump(d, open(sys.argv[1], "w")); os.chmod(sys.argv[1], 0o600)
print("access token valid for h:", round((o["expiresAt"] / 1000 - time.time()) / 3600, 2))
PY
    docker exec "$n" mkdir -p /home/tester/.claude
    docker cp "$tmp" "$n:/home/tester/.claude/.credentials.json"
    docker exec "$n" bash -c 'chown -R tester:tester /home/tester/.claude && chmod 600 /home/tester/.claude/.credentials.json'
    docker exec -u tester "$n" bash -c '[ -f /home/tester/.claude.json ] || printf "{\"hasCompletedOnboarding\": true}\n" > /home/tester/.claude.json'
    echo "login handed to $n" ;;
  rm)
    for n in "$@"; do docker rm -f "$n" >/dev/null 2>&1 && echo "$n removed"; done ;;
  *)
    sed -n 2,20p "$0"; exit 2 ;;
esac
