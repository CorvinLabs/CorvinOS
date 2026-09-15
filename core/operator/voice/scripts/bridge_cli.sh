#!/usr/bin/env bash
# bridge_cli.sh — orchestrator for the multi-channel bridge.
#
# Manages the shared adapter plus the per-channel daemons (WhatsApp,
# Telegram, Discord) via systemd user-units.

set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGES="$PLUGIN_ROOT/bridges"
SYSTEMD_DIR="$HOME/.config/systemd/user"

CHANNELS=("whatsapp" "telegram" "discord")
ALL_UNITS=(
  "corvin-voice-bridge-adapter.service"
  "corvin-voice-bridge-whatsapp.service"
  "corvin-voice-bridge-telegram.service"
  "corvin-voice-bridge-discord.service"
)

GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'
ok()    { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
fail()  { printf "${RED}✗${NC} %s\n" "$*"; }

cmd_install() {
  mkdir -p "$SYSTEMD_DIR"
  cp "$BRIDGES/shared/systemd/corvin-voice-bridge-adapter.service"   "$SYSTEMD_DIR/"
  cp "$BRIDGES/whatsapp/systemd/corvin-voice-bridge-whatsapp.service" "$SYSTEMD_DIR/"
  cp "$BRIDGES/telegram/systemd/corvin-voice-bridge-telegram.service" "$SYSTEMD_DIR/"
  cp "$BRIDGES/discord/systemd/corvin-voice-bridge-discord.service"   "$SYSTEMD_DIR/"
  systemctl --user daemon-reload
  ok "User-Units installed in $SYSTEMD_DIR"
}

cmd_status() {
  echo "Multi-Channel-Bridge:"
  for u in "${ALL_UNITS[@]}"; do
    state="$(systemctl --user is-active "$u" 2>/dev/null || echo unknown)"
    case "$state" in
      active)    color="$GREEN" ;;
      activating)color="$YELLOW" ;;
      inactive|failed) color="$RED" ;;
      *)         color="$NC" ;;
    esac
    printf "  %-44s ${color}%s${NC}\n" "$u" "$state"
  done
  echo
  for ch in "${CHANNELS[@]}"; do
    case "$ch" in
      whatsapp) port=7891 ;;
      telegram) port=7892 ;;
      discord)  port=7893 ;;
    esac
    if curl -s --max-time 1 "http://127.0.0.1:$port/status" 2>/dev/null | python3 -m json.tool 2>/dev/null | sed "s/^/  $ch: /"; then
      :
    fi
  done
}

cmd_enable() {
  systemctl --user enable --now corvin-voice-bridge-adapter.service
  for ch in "${CHANNELS[@]}"; do
    settings="$BRIDGES/$ch/settings.json"
    # Skip channels that have no token configured.
    case "$ch" in
      telegram) tok="$(jq -r '.telegram_token // empty' "$settings" 2>/dev/null)" ;;
      discord)  tok="$(jq -r '.discord_token  // empty' "$settings" 2>/dev/null)" ;;
      whatsapp) tok="present" ;; # WhatsApp uses auth/ creds, not a token
    esac
    if [[ -z "$tok" ]]; then
      warn "$ch: kein Token in $settings — Service wird nicht aktiviert."
      continue
    fi
    systemctl --user enable --now "corvin-voice-bridge-$ch.service"
    ok "corvin-voice-bridge-$ch enabled"
  done
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$(whoami)" 2>/dev/null || true
  fi
}

cmd_disable() {
  for u in "${ALL_UNITS[@]}"; do
    systemctl --user disable --now "$u" 2>/dev/null || true
  done
  ok "alle Services disabled"
}

cmd_restart() {
  systemctl --user restart "${ALL_UNITS[@]}"
  sleep 3
  cmd_status
}

cmd_log() {
  local n="${1:-50}"
  journalctl --user --no-pager -n "$n" -u corvin-voice-bridge-adapter \
                                       -u corvin-voice-bridge-whatsapp \
                                       -u corvin-voice-bridge-telegram \
                                       -u corvin-voice-bridge-discord
}

cmd_tail() {
  journalctl --user -f -u corvin-voice-bridge-adapter \
                       -u corvin-voice-bridge-whatsapp \
                       -u corvin-voice-bridge-telegram \
                       -u corvin-voice-bridge-discord
}

case "${1:-status}" in
  install) cmd_install ;;
  status)  cmd_status ;;
  enable)  cmd_enable ;;
  disable) cmd_disable ;;
  restart) cmd_restart ;;
  log)     shift; cmd_log "$@" ;;
  tail)    cmd_tail ;;
  *)
    echo "Usage: $0 {install|status|enable|disable|restart|log [n]|tail}"
    exit 2
    ;;
esac
