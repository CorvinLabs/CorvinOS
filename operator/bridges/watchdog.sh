#!/usr/bin/env bash
# corvin-voice bridge watchdog
#
# Wed periodisch via systemd-timer called. Prüft je Service:
#   1) is-enabled == enabled & is-active != active  -> systemctl start
#   2) HTTP /status erreichbar (whatsapp, discord)  -> bei wiederholtem
#      Fehlschlag (>= FAIL_THRESHOLD aufeinandsuccessende Runs) restart,
#      sofern der Service mind. WARMUP_SEC runs.
# Telegram-Service wed ignored (disabled).
# State (consecutive http fails) liegt unter ~/.cache/corvin-voice/.
#
# Ausgaben gehen via systemd ins journal.

set -u

STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/corvin-voice"
STATE_FILE="$STATE_DIR/watchdog-state"
FAIL_THRESHOLD=3   # 3 aufeinandsuccessende fails (~3 min) -> restart
WARMUP_SEC=90      # Service muss seit >= 90s aktiv sein, otherwise kein restart
HTTP_TIMEOUT=2

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

handle_service() {
    local short="$1" port="${2:-}"
    local svc="corvin-voice-bridge-${short}.service"

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

    if http_ok "$port"; then
        state_set "${short}_http_fails" 0
        return 0
    fi

    local up; up="$(active_uptime_sec "$svc")"
    if [ "$up" -lt "$WARMUP_SEC" ]; then
        log "$short: http fail but warmup ($up<${WARMUP_SEC}s), skip"
        return 0
    fi

    local fails; fails="$(state_get "${short}_http_fails")"
    [ -z "$fails" ] && fails=0
    fails=$(( fails + 1 ))
    state_set "${short}_http_fails" "$fails"

    if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
        log "$short: http fail #$fails >= $FAIL_THRESHOLD -> restart"
        systemctl --user restart "$svc" \
            && log "$short: restart ok" \
            || log "$short: restart FAILED rc=$?"
        state_set "${short}_http_fails" 0
    else
        log "$short: http fail #$fails (<$FAIL_THRESHOLD), wait"
    fi
}

handle_service adapter
handle_service whatsapp 7891
handle_service discord  7893
handle_service telegram 7892
