#!/usr/bin/env bash
# bridge.sh — Multi-Channel-Bridge orchestrator.
#
# Two run modes, picked automatically based on what the host supports:
#
#   1. systemd mode (Linux, WSL2 with systemd=true)
#      `up` installs user units, enables linger so they survive logout/reboot,
#      starts adapter + every configured channel as systemd services.
#
#   2. foreground mode (macOS, any host without systemd)
#      `fg` starts adapter + every configured channel inline in the current
#      terminal. Ctrl-C tears them all down. No persistence across reboots.
#      Use this on macOS, or on Linux if you don't want systemd.
#
# Usage:
#   bridge.sh up            — install + start all configured channels (systemd)
#   bridge.sh down          — stop + disable all services (systemd)
#   bridge.sh status        — overview of all channels and services
#   bridge.sh restart       — restart all running services (systemd)
#   bridge.sh logs [n]      — last N lines from journalctl (default 50)
#   bridge.sh tail          — live log stream
#   bridge.sh fg            — foreground run (works everywhere; Ctrl-C to stop)
#   bridge.sh doctor        — prerequisite check, no changes

set -uo pipefail

BRIDGES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# operator/voice is the voice plugin root (was plugins/voice before ADR-0035)
PLUGIN_ROOT="$(cd "$BRIDGES_DIR/../voice" && pwd)"
SCRIPTS_DIR="$PLUGIN_ROOT/scripts"
SYSTEMD_DIR="$HOME/.config/systemd/user"

# ── Debug logging defaults (unified across python + node) ─────────────
# Default CORVIN_DEBUG=1 so a fresh `bridge.sh up` writes verbose logs
# to <corvin_home>/logs/corvin.log. Operators flip off with
# `CORVIN_DEBUG=0 bridge.sh restart`. CORVIN_LOG_LEVEL overrides
# outright (DEBUG / INFO / WARNING / ERROR).
export CORVIN_DEBUG="${CORVIN_DEBUG:-0}"
# Default the log file under the same root the rest of the stack uses.
# Resolution: CORVIN_HOME wins, then <repo>/.corvin, then ~/.corvin.
_resolve_corvin_log_dir() {
  if [[ -n "${CORVIN_HOME:-}" ]]; then printf '%s/logs' "$CORVIN_HOME"; return; fi
  local repo="$BRIDGES_DIR"
  while [[ -n "$repo" && "$repo" != "/" ]]; do
    if [[ -f "$repo/.corvin_repo" ]] || [[ -d "$repo/plugins" ]]; then
      printf '%s/.corvin/logs' "$repo"
      return
    fi
    repo="$(dirname "$repo")"
  done
  printf '%s/.corvin/logs' "$HOME"
}
CORVIN_LOG_DIR="$(_resolve_corvin_log_dir)"
mkdir -p "$CORVIN_LOG_DIR" 2>/dev/null || true
export CORVIN_LOG_FILE="${CORVIN_LOG_FILE:-$CORVIN_LOG_DIR/corvin.log}"

UNIT_ADAPTER="corvin-voice-bridge-adapter.service"
UNIT_WA="corvin-voice-bridge-whatsapp.service"
UNIT_TG="corvin-voice-bridge-telegram.service"
UNIT_DC="corvin-voice-bridge-discord.service"
UNIT_SK="corvin-voice-bridge-slack.service"
UNIT_EM="corvin-voice-bridge-email.service"
# Phase 7 — corvin-* units removed; corvin-* units are the only variants.
UNIT_CORVIN_TIMEOUT_SVC="corvin-session-timeout.service"
UNIT_CORVIN_TIMEOUT_TIMER="corvin-session-timeout.timer"
UNIT_CORVIN_AUDIT_VERIFY_SVC="corvin-audit-verify.service"
UNIT_CORVIN_AUDIT_VERIFY_TIMER="corvin-audit-verify.timer"
# Layer 26 — daily user-style learner sweep at 04:45.
UNIT_CORVIN_USER_STYLE_SVC="corvin-user-style.service"
UNIT_CORVIN_USER_STYLE_TIMER="corvin-user-style.timer"
# Layer 30 (ADR-0020 Phase 30.2) — daily refusal-canary at 06:00.
# Probes installed engines for refusal-bypass drift; persists per-class
# scores + emits canary_drift_detected on signal-loss versus the rolling
# 30-day baseline.
UNIT_CORVIN_ENGINE_CANARY_SVC="corvin-engine-canary.service"
UNIT_CORVIN_ENGINE_CANARY_TIMER="corvin-engine-canary.timer"
# Layer 31 (ADR-0021 Phase 31.3) — supply-chain surveillance.
# Weekly digest (Mon 05:00) for MEDIUM/HIGH/CRITICAL findings + daily
# CRITICAL-diff (05:00) for newly-published critical CVEs. Per-event
# allow-list in supply_chain_verify.py.
UNIT_CORVIN_SUPPLY_CHAIN_WEEKLY_SVC="corvin-supply-chain-weekly.service"
UNIT_CORVIN_SUPPLY_CHAIN_WEEKLY_TIMER="corvin-supply-chain-weekly.timer"
UNIT_CORVIN_SUPPLY_CHAIN_CRITICAL_SVC="corvin-supply-chain-critical.service"
UNIT_CORVIN_SUPPLY_CHAIN_CRITICAL_TIMER="corvin-supply-chain-critical.timer"
# WebUI — uvicorn host for gateway + corvin-console
# (Phase G of the Corvin rollout). The systemd deployment runs WITHOUT
# uvicorn --reload: the console is the operator command-centre and --reload
# drops every live chat WebSocket (code 1012) whenever a chat turn edits repo
# code under core/console|core/gateway. Apply new code with
# `systemctl --user restart corvin-webui`; for a dev host with auto-reload use
# `CORVIN_CONSOLE_RELOAD=1 bridge.sh console`.
UNIT_CORVIN_WEBUI="corvin-webui.service"
UNIT_WATCHDOG_SVC="corvin-voice-bridge-watchdog.service"
UNIT_WATCHDOG_TIMER="corvin-voice-bridge-watchdog.timer"
UNIT_CORVIN_HERMES_HEALTH_SVC="corvin-hermes-health.service"
UNIT_CORVIN_HERMES_HEALTH_TIMER="corvin-hermes-health.timer"
ALL_UNITS=("$UNIT_ADAPTER" "$UNIT_WA" "$UNIT_TG" "$UNIT_DC" "$UNIT_SK" "$UNIT_EM" \
           "$UNIT_WATCHDOG_TIMER" "$UNIT_WATCHDOG_SVC" \
           "$UNIT_CORVIN_TIMEOUT_TIMER" "$UNIT_CORVIN_TIMEOUT_SVC" \
           "$UNIT_CORVIN_AUDIT_VERIFY_TIMER" "$UNIT_CORVIN_AUDIT_VERIFY_SVC" \
           "$UNIT_CORVIN_USER_STYLE_TIMER" "$UNIT_CORVIN_USER_STYLE_SVC" \
           "$UNIT_CORVIN_ENGINE_CANARY_TIMER" "$UNIT_CORVIN_ENGINE_CANARY_SVC" \
           "$UNIT_CORVIN_SUPPLY_CHAIN_WEEKLY_TIMER" "$UNIT_CORVIN_SUPPLY_CHAIN_WEEKLY_SVC" \
           "$UNIT_CORVIN_SUPPLY_CHAIN_CRITICAL_TIMER" "$UNIT_CORVIN_SUPPLY_CHAIN_CRITICAL_SVC" \
           "$UNIT_CORVIN_HERMES_HEALTH_TIMER" "$UNIT_CORVIN_HERMES_HEALTH_SVC" \
           "$UNIT_CORVIN_WEBUI")

# Resolve absolute paths to node + python so systemd's empty PATH doesn't
# fall through to a system version that lacks the deps (Baileys needs Node
# 19+, adapter needs `openai` installed in the python it invokes).
#
# Override order: $NODE_BIN / $PY_BIN env vars > nvm default node > `command -v node`
#                 $PY_BIN env > `command -v python3`
_resolve_node() {
  if [[ -n "${NODE_BIN:-}" && -x "$NODE_BIN" ]]; then printf '%s' "$NODE_BIN"; return; fi
  # Honour an active nvm without sourcing it.
  if [[ -d "$HOME/.nvm/versions/node" ]]; then
    local latest
    latest="$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | sort -V | tail -1)"
    if [[ -n "$latest" && -x "$HOME/.nvm/versions/node/$latest/bin/node" ]]; then
      printf '%s' "$HOME/.nvm/versions/node/$latest/bin/node"; return
    fi
  fi
  command -v node 2>/dev/null || true
}
_resolve_npm() {
  local node_bin; node_bin="$(_resolve_node)"
  if [[ -n "$node_bin" ]]; then
    local d; d="$(dirname "$node_bin")"
    [[ -x "$d/npm" ]] && { printf '%s' "$d/npm"; return; }
  fi
  command -v npm 2>/dev/null || true
}
_resolve_python() {
  if [[ -n "${PY_BIN:-}" && -x "$PY_BIN" ]]; then printf '%s' "$PY_BIN"; return; fi
  command -v python3 2>/dev/null || true
}
NODE_BIN="$(_resolve_node)"
NPM_BIN="$(_resolve_npm)"
PY_BIN="$(_resolve_python)"

GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; CYAN='\033[1;36m'; NC='\033[0m'
ok()    { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
fail()  { printf "${RED}✗${NC} %s\n" "$*"; }
step()  { printf "\n${CYAN}== %s ==${NC}\n" "$*"; }

# Open a URL in the system default browser — cross-platform, silent on headless.
# Usage: _open_browser <url> [delay_seconds]
# The optional delay lets the server start before the browser loads the page.
_open_browser() {
  local url="$1"
  local delay="${2:-0}"
  (
    [[ "$delay" -gt 0 ]] && sleep "$delay"
    if [[ "$(uname -s)" == Darwin ]]; then
      open "$url" 2>/dev/null || true
    elif [[ -n "${WSL_DISTRO_NAME:-}${WSL_INTEROP:-}" ]]; then
      # WSL2 — open in the Windows default browser
      cmd.exe /c start "" "$url" 2>/dev/null \
        || /mnt/c/Windows/System32/cmd.exe /c start "" "$url" 2>/dev/null \
        || true
    elif [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
      xdg-open "$url" 2>/dev/null || true
    fi
    # Headless / SSH / no display — skip silently
  ) &
}

# ── prerequisite check (used by `up` and `doctor`) ─────────────────────────
check_prereqs() {
  local missing=0
  [[ -x "$NODE_BIN" ]] && ok "node $($NODE_BIN --version) at $NODE_BIN" \
    || { fail "node fehlt: $NODE_BIN — installier Node 22 via nvm"; missing=1; }
  [[ -x "$PY_BIN" ]] && ok "python3 $($PY_BIN --version 2>&1 | awk '{print $2}') at $PY_BIN" \
    || { fail "python3 fehlt: $PY_BIN — installier Anaconda oder passe PY_BIN an"; missing=1; }
  if "$PY_BIN" -c "import openai" 2>/dev/null; then ok "python openai-package present"
  else fail "openai-package fehlt im python: $PY_BIN -m pip install openai"; missing=1; fi
  command -v claude >/dev/null && ok "claude CLI: $(claude --version 2>&1 | head -1)" \
    || { fail "claude CLI fehlt — Claude Code installieren"; missing=1; }
  command -v jq >/dev/null && ok "jq" || { fail "jq fehlt — apt install jq"; missing=1; }
  command -v curl >/dev/null && ok "curl" || { fail "curl fehlt"; missing=1; }
  if grep -qE '^[[:space:]]*OPENAI_API_?KEY=' "$PLUGIN_ROOT/../../.env" 2>/dev/null \
     || [[ -n "${OPENAI_API_KEY:-}" ]]; then
    ok "OPENAI_API_KEY set (TTS + Whisper funktionieren)"
  else
    warn "OPENAI_API_KEY nicht set — Voice-Notes (rein/raus) funktionieren nicht"
  fi
  return $missing
}

ensure_npm_modules() {
  local channel="$1"
  local dir="$BRIDGES_DIR/$channel"
  [[ -f "$dir/package.json" ]] || return 0
  if [[ ! -d "$dir/node_modules" ]] || [[ ! -d "$dir/node_modules/.package-lock.json" && ! -f "$dir/package-lock.json" ]]; then
    step "$channel: npm install"
    (cd "$dir" && "$NPM_BIN" install --no-audit --no-fund 2>&1 | tail -3)
    ok "$channel/node_modules ready"
  else
    ok "$channel/node_modules present"
  fi
}

# Channel only enabled if it has a usable credential.
channel_configured() {
  local ch="$1"
  case "$ch" in
    telegram) [[ -n "$(jq -r '.telegram_token // empty' "$BRIDGES_DIR/telegram/settings.json" 2>/dev/null)" ]] ;;
    discord)  [[ -n "$(jq -r '.discord_token // empty'  "$BRIDGES_DIR/discord/settings.json" 2>/dev/null)" ]] ;;
    whatsapp) [[ -f "$BRIDGES_DIR/whatsapp/auth/creds.json" ]] ;;
    slack)
      local s="$BRIDGES_DIR/slack/settings.json"
      [[ -n "$(jq -r '.slack_bot_token // empty' "$s" 2>/dev/null)" ]] \
        && [[ -n "$(jq -r '.slack_app_token // empty' "$s" 2>/dev/null)" ]] \
        && ! grep -q "DEIN_SLACK" "$s" 2>/dev/null ;;
    email)
      local s="$BRIDGES_DIR/email/settings.json"
      [[ -n "$(jq -r '.imap_user // empty' "$s" 2>/dev/null)" ]] \
        && [[ -n "$(jq -r '.imap_password // empty' "$s" 2>/dev/null)" ]] \
        && ! grep -q "YOUR_IMAP_APP_PASSWORD\|YOUR_SMTP_APP_PASSWORD" "$s" 2>/dev/null ;;
  esac
}

install_units() {
  mkdir -p "$SYSTEMD_DIR"
  # Service templates ship with __PLUGIN_ROOT__ / __NODE_BIN__ / __PYTHON_BIN__
  # / __REPO_ROOT__ placeholders so the repo is portable. Replace them at
  # install time with the absolute paths we resolved above. Without this
  # step the units would try to exec literal "__NODE_BIN__" and fail.
  local _esc_root _esc_node _esc_py _esc_repo _esc_bridges _repo
  _repo="$(cd "$PLUGIN_ROOT/../.." && pwd)"
  _esc_root="$(printf '%s'    "$PLUGIN_ROOT"  | sed -e 's/[\/&]/\\&/g')"
  _esc_node="$(printf '%s'    "$NODE_BIN"     | sed -e 's/[\/&]/\\&/g')"
  _esc_py="$(printf '%s'      "$PY_BIN"       | sed -e 's/[\/&]/\\&/g')"
  _esc_repo="$(printf '%s'    "$_repo"        | sed -e 's/[\/&]/\\&/g')"
  _esc_bridges="$(printf '%s' "$BRIDGES_DIR"  | sed -e 's/[\/&]/\\&/g')"
  for unit in "$BRIDGES_DIR/shared/systemd/$UNIT_ADAPTER" \
              "$BRIDGES_DIR/shared/systemd/$UNIT_WATCHDOG_SVC" \
              "$BRIDGES_DIR/shared/systemd/$UNIT_WATCHDOG_TIMER" \
              "$BRIDGES_DIR/whatsapp/systemd/$UNIT_WA" \
              "$BRIDGES_DIR/telegram/systemd/$UNIT_TG" \
              "$BRIDGES_DIR/discord/systemd/$UNIT_DC" \
              "$BRIDGES_DIR/slack/systemd/$UNIT_SK" \
              "$BRIDGES_DIR/email/systemd/$UNIT_EM" \
              "$BRIDGES_DIR/systemd/$UNIT_CORVIN_HERMES_HEALTH_SVC" \
              "$BRIDGES_DIR/systemd/$UNIT_CORVIN_HERMES_HEALTH_TIMER" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_TIMEOUT_SVC" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_TIMEOUT_TIMER" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_AUDIT_VERIFY_SVC" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_AUDIT_VERIFY_TIMER" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_USER_STYLE_SVC" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_USER_STYLE_TIMER" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_ENGINE_CANARY_SVC" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_ENGINE_CANARY_TIMER" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_SUPPLY_CHAIN_WEEKLY_SVC" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_SUPPLY_CHAIN_WEEKLY_TIMER" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_SUPPLY_CHAIN_CRITICAL_SVC" \
              "$PLUGIN_ROOT/scripts/systemd/$UNIT_CORVIN_SUPPLY_CHAIN_CRITICAL_TIMER" \
              "$_repo/core/gateway/systemd/$UNIT_CORVIN_WEBUI"; do
    [[ -f "$unit" ]] || continue
    sed -e "s/__PLUGIN_ROOT__/${_esc_root}/g" \
        -e "s/__NODE_BIN__/${_esc_node}/g" \
        -e "s/__PYTHON_BIN__/${_esc_py}/g" \
        -e "s/__REPO_ROOT__/${_esc_repo}/g" \
        -e "s/__BRIDGES_DIR__/${_esc_bridges}/g" \
        "$unit" > "$SYSTEMD_DIR/$(basename "$unit")"
  done
  systemctl --user daemon-reload
  ok "systemd User-Units in $SYSTEMD_DIR installiert (paths resolved)"
}

cmd_up() {
  step "1/5  prerequisites"
  check_prereqs || { fail "prerequisites missing — `bridge.sh doctor` zeigt's nochmal."; exit 1; }

  step "2/5  node modules per channel"
  for ch in telegram discord whatsapp slack email; do
    channel_configured "$ch" 2>/dev/null && ensure_npm_modules "$ch" || warn "$ch: not configured (skipped)"
  done

  step "3/5  install systemd units"
  install_units

  step "4/5  enable linger (services survive logout/reboot)"
  if loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
    ok "linger already on"
  else
    loginctl enable-linger "$USER" 2>/dev/null && ok "linger enabled" \
      || warn "linger konnte nicht enabled werden (sudo needed?). Services laufen trotzdem solange du eingeloggt bist."
  fi

  step "5/5  enable + start services"
  systemctl --user enable --now "$UNIT_ADAPTER"
  ok "adapter enabled+running"
  if systemctl --user enable --now "$UNIT_WATCHDOG_TIMER" 2>/dev/null; then
    ok "bridge watchdog timer enabled (60s heal cycle)"
  else
    warn "bridge watchdog timer could not be enabled"
  fi
  # Layer 8: daily session-timeout sweep. The timer fires the oneshot
  # service every 03:30; the service itself is short-lived and never
  # blocks the bridge. Best-effort enable — if the unit failed to land
  # we don't fail the whole `up`.
  if systemctl --user enable --now "$UNIT_CORVIN_TIMEOUT_TIMER" 2>/dev/null; then
    ok "corvin session-timeout timer enabled (daily 03:30)"
  else
    warn "corvin session-timeout timer could not be enabled — sweep falls back to manual"
  fi
  # Roadmap L: daily audit-chain verify at 04:30. If the chain breaks
  # the service writes one outbox envelope per relay target so the
  # operator gets pinged on Telegram / Discord / WhatsApp / Slack /
  # Email. Best-effort enable — if it fails, manual `voice-audit
  # verify` still works.
  if systemctl --user enable --now "$UNIT_CORVIN_AUDIT_VERIFY_TIMER" 2>/dev/null; then
    ok "corvin audit-verify timer enabled (daily 04:30)"
  else
    warn "corvin audit-verify timer could not be enabled — run 'voice-audit verify' manually"
  fi
  # Layer 26 — autonomous user-style learner sweep (daily 04:45).
  if systemctl --user enable --now "$UNIT_CORVIN_USER_STYLE_TIMER" 2>/dev/null; then
    ok "corvin user-style learner timer enabled (daily 04:45)"
  else
    warn "corvin user-style timer could not be enabled — module may run on next manual sweep"
  fi
  # Layer 30 (ADR-0020 Phase 30.2) — daily refusal-canary at 06:00.
  # Probes installed engines for refusal-bypass drift and persists per-
  # class scores. Drift-detection runs adapter-side on every spawn (read-
  # only); the daily probe-run is what GENERATES the score history.
  if systemctl --user enable --now "$UNIT_CORVIN_ENGINE_CANARY_TIMER" 2>/dev/null; then
    ok "corvin engine-canary timer enabled (daily 06:00)"
  else
    warn "corvin engine-canary timer could not be enabled — operator can run \`engine_canary.py run\` manually"
  fi
  # Layer 31 (ADR-0021 Phase 31.3) — supply-chain CVE surveillance.
  # Weekly digest (Mon 05:00) and daily CRITICAL-diff (05:00). Both
  # require pip-audit + npm audit on PATH; missing tooling triggers
  # cve_check_skipped audit warning instead of failing.
  if systemctl --user enable --now "$UNIT_CORVIN_SUPPLY_CHAIN_WEEKLY_TIMER" 2>/dev/null; then
    ok "corvin supply-chain weekly timer enabled (Mon 05:00)"
  else
    warn "corvin supply-chain weekly timer could not be enabled"
  fi
  if systemctl --user enable --now "$UNIT_CORVIN_SUPPLY_CHAIN_CRITICAL_TIMER" 2>/dev/null; then
    ok "corvin supply-chain critical-diff timer enabled (daily 05:00)"
  else
    warn "corvin supply-chain critical-diff timer could not be enabled"
  fi
  # Hermes health check & repair (every 5 minutes, ACO L5 + Tier LOCAL fallback).
  # Ensures Ollama is reachable and the qwen3 model is available; auto-repairs
  # if necessary (requires CORVIN_ACO_L5_RISKY=1). This guarantees a working
  # fallback engine for OS turns even if Claude Code becomes unavailable.
  UNIT_CORVIN_HERMES_HEALTH="corvin-hermes-health.service"
  UNIT_CORVIN_HERMES_HEALTH_TIMER="corvin-hermes-health.timer"
  if systemctl --user enable --now "$UNIT_CORVIN_HERMES_HEALTH_TIMER" 2>/dev/null; then
    ok "corvin hermes-health timer enabled (every 5 minutes)"
  else
    warn "corvin hermes-health timer could not be enabled — run 'bash $BRIDGES_DIR/setup-hermes-pib.sh --check' to diagnose"
  fi
  # WebUI host — uvicorn serving corvin-console under the gateway ASGI
  # app on 127.0.0.1:8765. Runs WITHOUT --reload so the command-centre chat
  # WebSocket stays stable; apply new code with a systemctl restart.
  # The unit's ExecStartPre auto-bootstraps the console venv when absent
  # (fresh clone, first boot after install), so no manual bootstrap step is
  # required.  TimeoutStartSec=300 gives pip enough time to install packages.
  if systemctl --user enable --now "$UNIT_CORVIN_WEBUI"; then
    ok "corvin WebUI enabled (uvicorn 127.0.0.1:8765, stable — no hot-reload)"
  else
    warn "corvin WebUI could not be enabled — check: journalctl --user -u $UNIT_CORVIN_WEBUI"
  fi
  for ch_unit_pair in "whatsapp:$UNIT_WA" "telegram:$UNIT_TG" "discord:$UNIT_DC" "slack:$UNIT_SK" "email:$UNIT_EM"; do
    ch="${ch_unit_pair%%:*}"; unit="${ch_unit_pair##*:}"
    if channel_configured "$ch" 2>/dev/null; then
      systemctl --user enable --now "$unit"
      ok "$ch enabled+running"
    else
      systemctl --user disable --now "$unit" 2>/dev/null || true
      warn "$ch: no token/auth -> service inactive (docs at $BRIDGES_DIR/$ch/settings.json)"
    fi
  done

  echo
  cmd_status
  echo
  ok "Bridge running. send a message von Telegram/Discord/WhatsApp — replies come back automatically."
  echo "Logs:   $0 logs"
  echo "Tail:   $0 tail"
  echo "Stop: $0 down"
}

cmd_down() {
  for u in "${ALL_UNITS[@]}"; do
    systemctl --user disable --now "$u" 2>/dev/null || true
  done
  ok "all bridge services stopped"
}

cmd_status() {
  echo "Multi-Channel-Bridge Status:"
  printf "  ${CYAN}debug:${NC}      CORVIN_DEBUG=%s  level=%s\n" \
    "${CORVIN_DEBUG:-1}" \
    "${CORVIN_LOG_LEVEL:-$([[ "${CORVIN_DEBUG:-1}" =~ ^(1|true|yes|on)$ ]] && echo DEBUG || echo INFO)}"
  printf "  ${CYAN}log file:${NC}   %s\n" "$CORVIN_LOG_FILE"
  if [[ -f "$CORVIN_LOG_FILE" ]]; then
    local sz; sz="$(stat -c %s "$CORVIN_LOG_FILE" 2>/dev/null || stat -f %z "$CORVIN_LOG_FILE" 2>/dev/null || echo ?)"
    printf "  ${CYAN}log size:${NC}   %s bytes\n" "$sz"
  fi
  echo
  for u in "${ALL_UNITS[@]}"; do
    state="$(systemctl --user is-active "$u" 2>/dev/null || echo unknown)"
    enabled="$(systemctl --user is-enabled "$u" 2>/dev/null || echo unknown)"
    case "$state" in
      active)    color="$GREEN" ;;
      activating)color="$YELLOW" ;;
      *)         color="$RED" ;;
    esac
    printf "  %-44s ${color}%-12s${NC} (enabled=%s)\n" "$u" "$state" "$enabled"
  done
  echo
  echo "HTTP-Health-Endpoints:"
  for ch_port in "whatsapp:7891" "telegram:7892" "discord:7893" "slack:7894" "email:7895"; do
    ch="${ch_port%%:*}"; port="${ch_port##*:}"
    out="$(curl -s --max-time 1 "http://127.0.0.1:$port/status" 2>/dev/null)"
    if [[ -n "$out" ]]; then
      printf "  %-10s " "$ch"
      echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); print(' '.join(f'{k}={v}' for k,v in d.items()))"
    else
      printf "  %-10s ${RED}offline${NC}\n" "$ch"
    fi
  done
}

cmd_restart() {
  for u in "${ALL_UNITS[@]}"; do
    systemctl --user is-active "$u" >/dev/null 2>&1 && systemctl --user restart "$u"
  done
  sleep 3
  cmd_status
}

# Resolve a writable log file the foreground daemons append to. Same path
# voice_lib.sh uses for VOICE_LOG_FILE — XDG-aware so macOS ~/Library is
# so honored if the user sets XDG_CONFIG_HOME.
_voice_log_path() {
  local cfg_dir="${XDG_CONFIG_HOME:-$HOME/.config}/corvin-voice"
  printf '%s\n' "$cfg_dir/voice.log"
}

cmd_logs() {
  local n="${1:-50}"
  if has_systemd; then
    journalctl --user --no-pager -n "$n" \
      -u "$UNIT_ADAPTER" -u "$UNIT_WA" -u "$UNIT_TG" -u "$UNIT_DC" -u "$UNIT_SK" -u "$UNIT_EM"
  else
    local log
    log="$(_voice_log_path)"
    if [[ -f "$log" ]]; then
      tail -n "$n" "$log"
    else
      echo "kein systemd, und auch keine Log-file unter $log."
      echo "Tipp: starte die Bridge mit 'bash $0 fg', dann landen Logs dort."
      return 1
    fi
  fi
}

cmd_tail() {
  echo "(Ctrl-C to end)"
  if has_systemd; then
    journalctl --user -f \
      -u "$UNIT_ADAPTER" -u "$UNIT_WA" -u "$UNIT_TG" -u "$UNIT_DC" -u "$UNIT_SK" -u "$UNIT_EM"
  else
    local log
    log="$(_voice_log_path)"
    if [[ -f "$log" ]]; then
      tail -F "$log"
    else
      echo "kein systemd, und auch keine Log-file unter $log."
      echo "Tipp: starte die Bridge mit 'bash $0 fg', dann landen Logs dort."
      return 1
    fi
  fi
}

cmd_doctor() {
  check_prereqs
  echo
  # HTTP health-probe — works without systemd. On macOS where the watchdog
  # timer isn't installed, this is the manual one-shot equivalent: run it
  # whenever you suspect a daemon got stuck. Each daemon exposes /status on
  # a fixed port and a hung daemon returns nothing within 1 s.
  echo "HTTP-Health-Endpoints (works on every host, no systemd needed):"
  local any_offline=0
  for ch_port in "whatsapp:7891" "telegram:7892" "discord:7893" "slack:7894" "email:7895"; do
    local ch="${ch_port%%:*}" port="${ch_port##*:}"
    local out
    out="$(curl -s --max-time 1 "http://127.0.0.1:$port/status" 2>/dev/null)"
    if [[ -n "$out" ]]; then
      printf "  %-10s " "$ch"
      echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); print(' '.join(f'{k}={v}' for k,v in d.items()))" 2>/dev/null \
        || echo "$out"
    else
      printf "  %-10s ${RED}offline${NC} (port %s)\n" "$ch" "$port"
      any_offline=1
    fi
  done
  if (( any_offline )); then
    echo
    if has_systemd; then
      printf "→ To restart offline channels:  bash %s restart\n" "$0"
    else
      printf "→ To restart in foreground:     bash %s fg\n" "$0"
    fi
  fi
  echo
  # Subsystem self-test: memory, audit chain, MCP servers, tenant tree,
  # vault, engines, license. CRITICAL failures here flip the doctor exit
  # code so CI / scripts can rely on a non-zero result.
  local self_test="$BRIDGES_DIR/shared/self_test.py"
  if [[ -f "$self_test" ]]; then
    local self_test_args=()
    # Forward --quick when caller asks for it, so the same command can be
    # used inside Docker HEALTHCHECK without paying for the full audit-verify.
    for a in "$@"; do
      case "$a" in
        --quick|--strict|--json) self_test_args+=("$a") ;;
      esac
    done
    if ! python3 "$self_test" "${self_test_args[@]}"; then
      return 1
    fi
  fi
}

# ── Platform-aware: detect whether systemd --user is available ──────────────
has_systemd() {
  command -v systemctl >/dev/null 2>&1 && systemctl --user list-units >/dev/null 2>&1
}

# ── Foreground run mode: portable across Linux / macOS / WSL2 ───────────────
# Starts adapter + every configured channel as child processes of this shell.
# Forwards SIGINT/SIGTERM so a single Ctrl-C tears everything down cleanly.
cmd_fg() {
  step "prerequisites"
  check_prereqs || { fail "prerequisites missing — bridge.sh doctor zeigt's nochmal."; exit 1; }

  step "node modules per channel"
  for ch in telegram discord whatsapp slack email; do
    channel_configured "$ch" 2>/dev/null && ensure_npm_modules "$ch" \
      || warn "$ch: not configured (skipped)"
  done

  PIDS=()
  cleanup() {
    echo
    step "stoppe Bridge (Ctrl-C empfangen)"
    for pid in "${PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
    sleep 0.5
    for pid in "${PIDS[@]}"; do
      kill -9 "$pid" 2>/dev/null || true
    done
    ok "alle Prozesse beendet"
    exit 0
  }
  trap cleanup INT TERM

  # Make sure the adapter sees the same OPENAI_API_KEY the systemd path would
  # have read from ~/.config/corvin-voice/service.env.
  ENV_FILE="$HOME/.config/corvin-voice/service.env"
  if [[ -f "$ENV_FILE" ]]; then
    set -a; . "$ENV_FILE"; set +a
    ok "loaded $ENV_FILE"
  fi

  # Resolve the claude CLI to an explicit absolute path and export it so EVERY
  # child (adapter, channel daemons, helper `claude -p` spawns, acs_runtime)
  # survives a stripped PATH. bridge.sh / systemd commonly run with a PATH that
  # lacks ~/.local/bin (where Claude Code installs the CLI). Without an explicit
  # pin the ADR-0159 OS-engine auto-detect would false-negative and silently
  # downgrade the OS turn to hermes → "hermes connect error: timed out" although
  # claude is installed. The Python resolver (helper_model.resolve_claude_bin)
  # already probes these locations; exporting the pin here closes the
  # environmental root so the guarantee is uniform across all children.
  if [[ -z "${CORVIN_CLAUDE_BIN:-}" ]]; then
    _cc_bin="$(command -v claude 2>/dev/null || true)"
    if [[ -z "$_cc_bin" ]]; then
      for _cand in "$HOME/.local/bin/claude" /usr/local/bin/claude \
                   /usr/bin/claude /opt/homebrew/bin/claude; do
        if [[ -x "$_cand" ]]; then _cc_bin="$_cand"; break; fi
      done
    fi
    if [[ -n "$_cc_bin" ]]; then
      export CORVIN_CLAUDE_BIN="$_cc_bin"
      ok "resolved CORVIN_CLAUDE_BIN=$_cc_bin"
    else
      warn "claude CLI not found — OS engine auto-detects (hermes fallback if truly absent)"
    fi
  else
    ok "CORVIN_CLAUDE_BIN already set: $CORVIN_CLAUDE_BIN"
  fi

  step "Starte adapter (foreground)"
  ( cd "$BRIDGES_DIR/shared" && exec "$PY_BIN" adapter.py ) &
  PIDS+=($!)
  ok "adapter pid=${PIDS[-1]}"

  for ch_pair in "whatsapp:$BRIDGES_DIR/whatsapp" \
                 "telegram:$BRIDGES_DIR/telegram" \
                 "discord:$BRIDGES_DIR/discord" \
                 "slack:$BRIDGES_DIR/slack" \
                 "email:$BRIDGES_DIR/email"; do
    ch="${ch_pair%%:*}"; dir="${ch_pair##*:}"
    if channel_configured "$ch" 2>/dev/null; then
      step "Starte $ch (foreground)"
      ( cd "$dir" && exec "$NODE_BIN" daemon.js ) &
      PIDS+=($!)
      ok "$ch pid=${PIDS[-1]}"
    else
      warn "$ch: not configured — skipped"
    fi
  done

  echo
  ok "Bridge is running im Vordergrund. Ctrl-C zum Beenden."
  echo
  # Wait for any child to exit; cleanup() handles the rest.
  wait -n "${PIDS[@]}" 2>/dev/null || true
  cleanup
}

cmd_console() {
  local repo_root
  repo_root="$(cd "$BRIDGES_DIR/../.." && pwd)"
  local console_dir="$repo_root/core/console"
  local venv="$console_dir/.venv"
  local bootstrap="$console_dir/bootstrap.sh"

  if [[ ! -d "$venv" ]]; then
    step "bootstrapping web console (first run)"
    if [[ -x "$bootstrap" ]]; then
      bash "$bootstrap" || { fail "bootstrap failed — check $bootstrap for errors"; exit 1; }
    else
      fail "Console not set up. Run: bash $bootstrap"
      exit 1
    fi
  fi

  # Build the same PYTHONPATH the systemd unit uses.
  local pypath="$repo_root/core/console:$repo_root/core/gateway:$repo_root/core/license:$repo_root/core/compliance:$repo_root/operator/forge:$repo_root/operator/skill-forge:$repo_root/operator/bridges/shared"

  local env_file="$HOME/.config/corvin-voice/service.env"
  if [[ -f "$env_file" ]]; then
    set -a; . "$env_file"; set +a
  fi

  echo
  ok "Starting Corvin web console on http://127.0.0.1:8765"
  echo "  Auto-login URL: http://127.0.0.1:8765/console/login"
  echo "  (opens your browser automatically — no token needed)"
  echo "  Press Ctrl-C to stop."
  echo

  # Open /console/login — the SPA LoginPage redirects to the API
  # local-login endpoint which sets the session cookie and bounces to /console/.
  _open_browser "http://127.0.0.1:8765/console/login" 2

  # Pin the runtime root for the whole console process. Without this,
  # every component resolves corvin_home() by cwd-walk and the daemon's
  # cwd depends on where the operator launched it — components inside ONE
  # process can then disagree with persisted session workdirs (observed:
  # ACS runs landing in ~/.corvin while sessions live in <repo>/.corvin).
  local corvin_home_resolved
  if [[ -n "${CORVIN_HOME:-}" ]]; then
    corvin_home_resolved="$CORVIN_HOME"
  elif [[ -d "$repo_root/.corvin" ]]; then
    corvin_home_resolved="$repo_root/.corvin"
  else
    corvin_home_resolved="$HOME/.corvin"
  fi

  # Hot-reload is OPT-IN only (CORVIN_CONSOLE_RELOAD=1). The console is the
  # operator command-centre: chat turns routinely edit repo code under
  # core/console / core/gateway, and uvicorn --reload closes every live
  # WebSocket with code 1012 (Service Restart) on each such change — which the
  # chat client surfaces as "Connection error — reconnecting…" after a task.
  # Defaulting reload OFF keeps the command-centre WS stable; developers who
  # want auto-reload opt in explicitly and accept the WS drops.
  local reload_args=()
  if [[ "${CORVIN_CONSOLE_RELOAD:-0}" == "1" ]]; then
    warn "CORVIN_CONSOLE_RELOAD=1 — hot-reload ON (live chat WebSockets will drop on every code change under core/console|core/gateway)"
    reload_args=(--reload --reload-dir "$repo_root/core/console" --reload-dir "$repo_root/core/gateway")
  fi

  CORVIN_HOME="$corvin_home_resolved" \
  PYTHONPATH="$pypath" \
  exec "$venv/bin/python" -m uvicorn corvin_gateway.app:app \
    --host 127.0.0.1 --port 8765 --log-level info \
    "${reload_args[@]}"
}

# Friendly fallback for the systemd-only commands when systemd isn't there.
require_systemd() {
  if ! has_systemd; then
    fail "systemd --user not available auf diesem Host."
    echo
    echo "  Du bist likely auf macOS oder WSL2 without systemd. Nutz stattdessen:"
    echo "    $0 fg              # startet alles im Vordergrund (Ctrl-C zum Beenden)"
    echo
    echo "  Auf Linux: stelle sicher, dass deine User-Session systemd --user hat:"
    echo "    systemctl --user list-units"
    exit 2
  fi
}

# Pull the latest plugin code before any startup-mutating action. Honors
# `autoupdate=false` in voice config; never destructive (skips on dirty tree
# or non-ff history). See scripts/autoupdate.sh.
maybe_autoupdate() {
  bash "$SCRIPTS_DIR/autoupdate.sh" 2>/dev/null || true
}

case "${1:-up}" in
  up)      maybe_autoupdate; require_systemd; cmd_up ;;
  down)    require_systemd; cmd_down ;;
  status)  if has_systemd; then cmd_status; else fail "kein systemd — nutz \`bridge.sh fg\`"; exit 2; fi ;;
  restart) maybe_autoupdate; require_systemd; cmd_restart ;;
  logs)    shift; cmd_logs "${1:-50}" ;;
  tail)    cmd_tail ;;
  fg)      maybe_autoupdate; cmd_fg ;;
  console) cmd_console ;;
  doctor)  shift; cmd_doctor "$@" ;;
  *)
    echo "Usage: $0 {up|down|status|restart|logs [n]|tail|fg|console|doctor}"
    echo
    echo "  systemd hosts (Linux, WSL2):  up | down | restart | status"
    echo "  any host (incl. macOS):       fg | console | logs | tail | doctor"
    echo
    echo "  console — start only the web UI (http://127.0.0.1:8765)"
    echo "            works without Claude Code; configure engines in-browser"
    exit 2
    ;;
esac
