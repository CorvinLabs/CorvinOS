#!/usr/bin/env bash
# Corvin container healthcheck. Exits 0 iff the always-on components
# (adapter + console) are responsive AND the boot-time self-test passes
# its CRITICAL checks (memory paths, audit chain, MCP modules, tenant
# tree, engines).

set -uo pipefail

# Gateway must answer /readyz on port 8000.
if ! curl --silent --fail --max-time 5 http://127.0.0.1:8000/readyz >/dev/null; then
    if ! curl --silent --fail --max-time 5 http://127.0.0.1:8000/healthz >/dev/null; then
        echo "gateway: not responding on 8000" >&2
        exit 1
    fi
fi

# Adapter writes a heartbeat file under /home/corvin/.corvin/run/ at
# least every 60s. If it's stale, the adapter is wedged.
HB="/home/corvin/.corvin/run/adapter.heartbeat"
if [[ -f "$HB" ]]; then
    age=$(( $(date +%s) - $(stat -c %Y "$HB") ))
    if (( age > 180 )); then
        echo "adapter: heartbeat stale ($age s)" >&2
        exit 1
    fi
fi

# Subsystem self-test in quick mode (~200 ms) — verifies that memory
# paths, audit file readability, MCP server modules, tenant home, vault
# permissions, and the engine CLI are all in working order. Critical
# failures here flip the container to `unhealthy`.
# Container layout: repo is at /opt/corvin-repo (Dockerfile line 64).
# Outside the container we resolve relative to this script's location.
SELF_TEST="/opt/corvin-repo/operator/bridges/shared/self_test.py"
if [[ ! -f "$SELF_TEST" ]]; then
    SELF_TEST="$(dirname "$0")/../operator/bridges/shared/self_test.py"
fi
if [[ -f "$SELF_TEST" ]]; then
    # stdout (JSON) → capture file; stderr (debug logs) → healthcheck stderr.
    # Keeping the two streams separate ensures /tmp/corvin-self-test.json is
    # always valid JSON when a human or a CI script needs to parse it post-mortem.
    PYTHON="${CORVIN_PYTHON:-/opt/corvin-venv/bin/python3}"
    if [[ ! -x "$PYTHON" ]]; then PYTHON="python3"; fi
    if ! "$PYTHON" "$SELF_TEST" --quick --json >/tmp/corvin-self-test.json; then
        echo "self-test: CRITICAL failure" >&2
        head -c 4096 /tmp/corvin-self-test.json >&2 || true
        exit 1
    fi
fi

exit 0
