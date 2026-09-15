#!/usr/bin/env bash
# whatsapp_cli.sh — start/stop/status the WhatsApp bridge (daemon + adapter).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=voice_lib.sh
source "$SCRIPT_DIR/voice_lib.sh"

WA_DIR="$(cd "$SCRIPT_DIR/../../bridges/whatsapp" && pwd)"
SHARED_DIR="$(cd "$SCRIPT_DIR/../../bridges/shared" && pwd)"
WA_PID_DIR="$VOICE_CONFIG_DIR/whatsapp"
mkdir -p "$WA_PID_DIR"
DAEMON_PID="$WA_PID_DIR/daemon.pid"
ADAPTER_PID="$WA_PID_DIR/adapter.pid"
HTTP_PORT="${WA_HTTP_PORT:-7891}"

is_running() { local f="$1"; [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null; }

cmd_status() {
  echo "WhatsApp-Bridge:"
  if is_running "$DAEMON_PID"; then
    echo "  daemon : RUNNING (pid=$(cat "$DAEMON_PID"))"
  else echo "  daemon : not running"; fi
  if is_running "$ADAPTER_PID"; then
    echo "  adapter: RUNNING (pid=$(cat "$ADAPTER_PID"))"
  else echo "  adapter: not running"; fi
  if is_running "$DAEMON_PID"; then
    if command -v curl >/dev/null 2>&1; then
      curl -s --max-time 2 "http://127.0.0.1:${HTTP_PORT}/status" 2>/dev/null \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  paired :", "yes" if d.get("paired") else "no"); print("  mock   :", d.get("mock")); print("  whitelist:", d.get("whitelist_size"), "entries"); print("  enabled chats:", d.get("enabled_chats", 0)); print("  pending outbox:", d.get("pending_outbox"))' \
        2>/dev/null || echo "  (HTTP API unreachable)"
    fi
  fi
}

# spawn_detached: start a background process whose own PID is also the
# leader of a fresh process group, so we can later kill it (and any
# children) with a single signal. setsid exists on Linux and most BSDs but
# is missing from a stock macOS install — fall back to plain `&` + disown
# in that case (no process-group, but still detached).
spawn_detached() {
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" >>"$VOICE_LOG_FILE" 2>&1 < /dev/null &
  else
    "$@" >>"$VOICE_LOG_FILE" 2>&1 < /dev/null &
  fi
}

cmd_on() {
  if [[ ! -d "$WA_DIR/node_modules" ]]; then
    echo "Node modules missing. Run: cd $WA_DIR && npm install"
    return 1
  fi
  if is_running "$DAEMON_PID"; then
    echo "daemon already running"
  else
    cd "$WA_DIR"
    spawn_detached node daemon.js
    echo "$!" > "$DAEMON_PID"
    disown $! 2>/dev/null || true
    echo "daemon started (pid=$(cat "$DAEMON_PID"))"
  fi
  if is_running "$ADAPTER_PID"; then
    echo "adapter already running"
  else
    cd "$SHARED_DIR"
    spawn_detached python3 adapter.py
    echo "$!" > "$ADAPTER_PID"
    disown $! 2>/dev/null || true
    echo "adapter started (pid=$(cat "$ADAPTER_PID"))"
  fi
}

cmd_off() {
  for f in "$DAEMON_PID" "$ADAPTER_PID"; do
    if [[ -f "$f" ]]; then
      pid="$(cat "$f")"
      if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null
        sleep 0.3
        kill -KILL "$pid" 2>/dev/null
      fi
      rm -f "$f"
    fi
  done
  echo "daemon and adapter stopped"
}

cmd_pair() {
  cd "$WA_DIR"
  echo "Starting pairing flow. Scan the QR with WhatsApp -> Linked Devices."
  exec node daemon.js --pair-only
}

cmd_pair_code() {
  # Use the 8-digit pairing-code flow instead of QR. Pull the phone number
  # from the first whitelist entry, or from the optional argument.
  local phone="${1:-}"
  if [[ -z "$phone" ]]; then
    phone="$(jq -r '.whitelist[0] // empty' "$WA_DIR/settings.json" 2>/dev/null | sed 's/@s.whatsapp.net$//')"
  fi
  if [[ -z "$phone" ]]; then
    echo "Bitte Nummer angeben: $0 pair-code 491729809432"
    echo "(oder eine Nummer in settings.json -> whitelist eintragen)"
    return 2
  fi
  cd "$WA_DIR"
  echo "Starting pair-code flow für $phone."
  echo "Auf dem Phone: WhatsApp → Einstellungen → Verknüpfte Geräte → Gerät hinzufügen → 'Mit Telefonnummer verknüpfen'."
  exec node daemon.js --pair-only --pair-code "$phone"
}

cmd_mock() {
  if is_running "$DAEMON_PID" || is_running "$ADAPTER_PID"; then
    echo "Real bridge running, stop it first with: $0 off"
    return 1
  fi
  cd "$WA_DIR"
  spawn_detached node daemon.js --mock
  echo "$!" > "$DAEMON_PID"
  disown $! 2>/dev/null || true
  spawn_detached python3 adapter.py
  echo "$!" > "$ADAPTER_PID"
  disown $! 2>/dev/null || true
  echo "mock daemon + adapter started"
  echo "POST to http://127.0.0.1:${HTTP_PORT}/mock/inbound to inject messages"
}

cmd_test() {
  # /whatsapp-test <nummer> — drop a manual outbox entry. Daemon picks it up
  # and sends "Hallo von der Bridge — Test." Useful to verify end-to-end
  # delivery without going through Claude.
  local target="${1:-}"
  if [[ -z "$target" ]]; then
    echo "usage: $0 test <jid>"
    echo "  jid format: 49170XXXXXXX@s.whatsapp.net"
    return 2
  fi
  local id="test_$(date +%s)"
  local out_file="$WA_DIR/outbox/${id}_00.json"
  printf '{"to":"%s","text":"Hallo von der Bridge — Test."}\n' "$target" > "$out_file"
  echo "queued test message for $target as $out_file"
  echo "(daemon will pick it up within ~500ms)"
}

cmd_history() {
  local n="${1:-10}"
  local hist_file="$WA_DIR/history.jsonl"
  if [[ ! -s "$hist_file" ]]; then
    # Reconstruct from the processed/ archive instead.
    if compgen -G "$WA_DIR/processed/*.json" >/dev/null; then
      ls -t "$WA_DIR/processed"/*.json 2>/dev/null | head -n "$n" | while read -r f; do
        local ts from text
        from="$(jq -r '.from // "?"' "$f")"
        text="$(jq -r '.text // .audio_path // "(no content)"' "$f" | tr '\n' ' ' | cut -c1-100)"
        ts="$(stat -c '%y' "$f" | cut -d. -f1)"
        printf '[%s] %s\n  %s\n' "$ts" "$from" "$text"
      done
    else
      echo "Keine Historie vorhanden."
    fi
    return 0
  fi
  tail -n "$n" "$hist_file" | jq -r '"[\(.ts)] \(.from)\n  \(.text)"'
}

cmd_tail() {
  local n="${1:-30}"
  echo "(strg-c zum Beenden)"
  tail -n "$n" -F "$VOICE_LOG_FILE" 2>/dev/null
}

cmd_install_service() {
  local user_dir="$HOME/.config/systemd/user"
  mkdir -p "$user_dir"
  cp "$WA_DIR/systemd/corvin-voice-wa-daemon.service"   "$user_dir/"
  cp "$WA_DIR/systemd/corvin-voice-wa-adapter.service"  "$user_dir/"
  systemctl --user daemon-reload
  echo "User-Units installed in $user_dir"
  echo "Enable + start:"
  echo "  $0 enable-service"
}

cmd_enable_service() {
  systemctl --user enable --now corvin-voice-wa-daemon.service
  systemctl --user enable --now corvin-voice-wa-adapter.service
  # Linger lets the user-services keep running after logout / over reboots.
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$(whoami)" 2>/dev/null || true
  fi
  echo "Services enabled. Status: $0 service-status"
}

cmd_disable_service() {
  systemctl --user disable --now corvin-voice-wa-daemon.service 2>/dev/null
  systemctl --user disable --now corvin-voice-wa-adapter.service 2>/dev/null
  echo "Services disabled."
}

cmd_service_status() {
  for u in corvin-voice-wa-daemon corvin-voice-wa-adapter; do
    echo "--- $u ---"
    systemctl --user status "$u.service" --no-pager -n 5 2>&1 | head -10
    echo
  done
}

case "${1:-status}" in
  on)              cmd_on ;;
  off)             cmd_off ;;
  status)          cmd_status ;;
  pair)            cmd_pair ;;
  pair-code)       shift; cmd_pair_code "$@" ;;
  mock)            cmd_mock ;;
  test)            shift; cmd_test "$@" ;;
  history)         shift; cmd_history "$@" ;;
  tail)            shift; cmd_tail "$@" ;;
  install-service) cmd_install_service ;;
  enable-service)  cmd_enable_service ;;
  disable-service) cmd_disable_service ;;
  service-status)  cmd_service_status ;;
  *) echo "usage: $0 {on|off|status|pair|mock|test|history|tail|install-service|enable-service|disable-service|service-status}"; exit 2 ;;
esac
