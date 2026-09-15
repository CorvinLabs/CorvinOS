#!/usr/bin/env bash
# corvin-voice bridge watchdog
#
# Wed periodisch via systemd-timer called. Prüft je Service:
#   1) is-enabled == enabled & is-active != active  -> systemctl start
#   2) HTTP /status erreichbar (whatsapp, discord)  -> bei wiederholtem
#      Fehlschlag (>= FAIL_THRESHOLD aufeinandsuccessende Runs) restart,
#      sofern der Service mind. WARMUP_SEC runs.
#   3) /status BODY (nicht nur HTTP-Code) auf Wedge-Signale geprüft
#      (2026-07-30): ein wedged-aber-lebender Prozess antwortet weiter mit
#      HTTP 200, während der eigentliche Outbox-Poller / die WhatsApp-
#      Verbindung längst hängt — genau der Fall, der 2026-07-27 einen
#      38-Minuten-Ausfall unbemerkt ließ (poller_stalled_s wurde vom alten
#      watchdog nie gelesen). STALL_THRESHOLD liegt bewusst über dem
#      WhatsApp-Reconnect-Backoff-Cap (60s), damit ein normaler Backoff-
#      Zyklus nie fälschlich als Wedge zählt.
# Telegram-Service wed ignored (disabled).
# State (consecutive http fails) liegt unter ~/.cache/corvin-voice/.
#
# Ausgaben gehen via systemd ins journal.

set -u

STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/corvin-voice"
STATE_FILE="$STATE_DIR/watchdog-state"
FAIL_THRESHOLD=3    # 3 aufeinandsuccessende fails (~3 min) -> restart
WARMUP_SEC=90       # Service muss seit >= 90s aktiv sein, otherwise kein restart
HTTP_TIMEOUT=2
STALL_THRESHOLD=90  # Sekunden — sicher über dem 60s WhatsApp-Backoff-Cap

mkdir -p "$STATE_DIR"
touch "$STATE_FILE"

log() { printf '[watchdog] %s\n' "$*"; }

state_get() {
    local key="$1"
    awk -F= -v k="$key" '$1==k {print $2; exit}' "$STATE_FILE"
}

state_set() {
    local key="$1" val="$2" tmp
    tmp="$(mktemp "$STATE_FILE.XXXXXX")"
    awk -F= -v k="$key" '$1!=k' "$STATE_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$STATE_FILE"
}

is_enabled()  { [ "$(systemctl --user is-enabled "$1" 2>/dev/null)" = "enabled" ]; }
is_active()   { systemctl --user is-active --quiet "$1"; }

active_uptime_sec() {
    local svc="$1" ts now
    ts="$(systemctl --user show "$svc" -p ActiveEnterTimestamp --value 2>/dev/null)"
    [ -z "$ts" ] && { echo 0; return; }
    ts="$(date -d "$ts" +%s 2>/dev/null)" || { echo 0; return; }
    now="$(date +%s)"
    echo $(( now - ts ))
}

http_ok() {
    local port="$1"
    curl -sf --max-time "$HTTP_TIMEOUT" "http://127.0.0.1:$port/status" >/dev/null 2>&1
}

# Fetches /status and prints the BODY (not just the exit code). Empty output
# on any failure (curl error, non-2xx, timeout) — callers treat empty as
# "couldn't check", same as an http_ok failure.
http_body() {
    local port="$1"
    curl -sf --max-time "$HTTP_TIMEOUT" "http://127.0.0.1:$port/status" 2>/dev/null
}

# Extracts one numeric field from a JSON status body. Prints 0 if the field
# is absent/non-numeric/the body is malformed — a missing signal must never
# be misread as "definitely stalled" (that would restart a healthy daemon
# whose /status shape simply doesn't have this field, e.g. adapter/telegram).
# python3 is already a hard dependency of this whole project, so this adds
# no new tooling requirement (avoids depending on jq being installed).
json_field() {
    local body="$1" field="$2"
    printf '%s' "$body" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    v = d.get('$field', 0)
    print(int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0)
except Exception:
    print(0)
" 2>/dev/null || echo 0
}

# Returns 0 (bash-true) if ANY of the given field names is present in the
# body and exceeds STALL_THRESHOLD seconds — a wedged-but-HTTP-alive daemon
# signal that a bare `curl -sf` (HTTP 200 either way) can never see.
is_stalled() {
    local body="$1"; shift
    local field val
    for field in "$@"; do
        val="$(json_field "$body" "$field")"
        if [ "$val" -ge "$STALL_THRESHOLD" ] 2>/dev/null; then
            log "  stall signal: $field=${val}s >= ${STALL_THRESHOLD}s"
            return 0
        fi
    done
    return 1
}

handle_service() {
    local short="$1" port="${2:-}"
    local svc="corvin-voice-bridge-${short}.service"
    # Remaining args (if any): JSON field names to check for a stall (see
    # is_stalled). Array-slice rather than `shift` — safe even when called
    # with only 1-2 args (e.g. `handle_service adapter`, no stall fields).
    local stall_fields=("${@:3}")

    if ! is_enabled "$svc"; then
        return 0
    fi

    if ! is_active "$svc"; then
        log "$short: inactive but enabled -> start"
        # Clear a start-limit-hit first: repeated daemon exits (e.g. network
        # flapping tripping StartLimitBurst) leave the unit 'failed' and a
        # bare start is rejected until the rate window expires (~10 min
        # blind). reset-failed makes the watchdog the recovery of last
        # resort it is meant to be.
        systemctl --user reset-failed "$svc" 2>/dev/null || true
        systemctl --user start "$svc" \
            && log "$short: start ok" \
            || log "$short: start FAILED rc=$?"
        state_set "${short}_http_fails" 0
        return 0
    fi

    [ -z "$port" ] && return 0

    local body; body="$(http_body "$port")"
    if [ -n "$body" ] && ! is_stalled "$body" "${stall_fields[@]}"; then
        state_set "${short}_http_fails" 0
        return 0
    fi

    local up; up="$(active_uptime_sec "$svc")"
    if [ "$up" -lt "$WARMUP_SEC" ]; then
        log "$short: http fail/stall but warmup ($up<${WARMUP_SEC}s), skip"
        return 0
    fi

    local fails; fails="$(state_get "${short}_http_fails")"
    [ -z "$fails" ] && fails=0
    fails=$(( fails + 1 ))
    state_set "${short}_http_fails" "$fails"

    if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
        if [ -z "$body" ]; then
            log "$short: http fail #$fails >= $FAIL_THRESHOLD -> restart"
        else
            log "$short: stalled #$fails >= $FAIL_THRESHOLD -> restart"
        fi
        systemctl --user restart "$svc" \
            && log "$short: restart ok" \
            || log "$short: restart FAILED rc=$?"
        state_set "${short}_http_fails" 0
    else
        log "$short: http fail/stall #$fails (<$FAIL_THRESHOLD), wait"
    fi
}

handle_service adapter
handle_service whatsapp 7891 disconnected_s
handle_service discord  7893 poller_stalled_s precheck_stalled_s
handle_service telegram 7892
