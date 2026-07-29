#!/usr/bin/env bash
# Tests for watchdog.sh's stall-detection logic (2026-07-30).
#
# Covers the specific gap this was written to close: the watchdog previously
# only checked HTTP response code (`curl -sf`), which a wedged-but-alive
# daemon still answers with 200 — the exact failure mode that let a hung
# Discord outbox poller run silently for 38 minutes (incident 2026-07-27,
# poller_stalled_s existed but nothing ever read it) and, separately, let a
# WhatsApp daemon hammer WhatsApp's servers on a fixed 1s reconnect retry
# with no backoff (incident 2026-07-29).
#
# Run: bash operator/bridges/test_watchdog_stall_detection.sh

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source the functions without running the bottom `handle_service` calls —
# temporarily blank `handle_service` invocations by only sourcing up to the
# function definitions via a trick: define handle_service as a no-op guard
# isn't needed since we source in a subshell that never reaches systemctl
# calls for a nonexistent unit anyway (is_enabled fails closed → return 0).
source "$SCRIPT_DIR/watchdog.sh" 2>/dev/null

pass=0
fail=0

check() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        printf '  ok  %s\n' "$desc"
        pass=$((pass + 1))
    else
        printf '  FAIL  %s (expected=%s actual=%s)\n' "$desc" "$expected" "$actual"
        fail=$((fail + 1))
    fi
}

echo "== json_field =="

v="$(json_field '{"poller_stalled_s": 45}' poller_stalled_s)"
check "extracts an integer field" "45" "$v"

v="$(json_field '{"poller_stalled_s": 0}' poller_stalled_s)"
check "extracts zero correctly" "0" "$v"

v="$(json_field '{"other_field": 45}' poller_stalled_s)"
check "missing field defaults to 0 (never misread as stalled)" "0" "$v"

v="$(json_field 'not json at all {{{' poller_stalled_s)"
check "malformed JSON defaults to 0, does not crash" "0" "$v"

v="$(json_field '{"poller_stalled_s": "not a number"}' poller_stalled_s)"
check "non-numeric value defaults to 0" "0" "$v"

echo
echo "== is_stalled =="

is_stalled '{"poller_stalled_s": 0, "precheck_stalled_s": 0}' poller_stalled_s precheck_stalled_s
check "healthy body (both 0) is NOT stalled" "1" "$?"

is_stalled '{"poller_stalled_s": 200, "precheck_stalled_s": 0}' poller_stalled_s precheck_stalled_s
check "one field far over threshold IS stalled" "0" "$?"

is_stalled '{"poller_stalled_s": 0, "precheck_stalled_s": 200}' poller_stalled_s precheck_stalled_s
check "the OTHER field over threshold also counts (either-field-trips)" "0" "$?"

is_stalled '{"disconnected_s": 45}' disconnected_s
check "45s disconnect (mid legitimate 60s-cap backoff) is NOT stalled" "1" "$?"

is_stalled '{"disconnected_s": 89}' disconnected_s
check "89s disconnect (just under threshold) is NOT stalled" "1" "$?"

is_stalled '{"disconnected_s": 90}' disconnected_s
check "90s disconnect (at threshold) IS stalled" "0" "$?"

is_stalled '{"disconnected_s": 300}' disconnected_s
check "300s disconnect (genuinely wedged) IS stalled" "0" "$?"

is_stalled '{"paired": true}'
check "no stall fields given (adapter/telegram case) is NEVER stalled" "1" "$?"

echo
echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
