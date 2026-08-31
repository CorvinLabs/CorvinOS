#!/usr/bin/env bash
# install-systemd.sh — install the Corvin Operator UI as a
# system-wide systemd service + browser-auto-open on desktop login.
#
# Implements ADR-0037 § "Always-on + auto-open".
#
# Usage:
#   sudo bash core/console/install-systemd.sh                  # install
#   sudo bash core/console/install-systemd.sh --uninstall      # clean revert
#   bash      core/console/install-systemd.sh --user-mode      # per-user (no sudo)
#   sudo bash core/console/install-systemd.sh --no-autostart   # systemd only, no browser
#   sudo bash core/console/install-systemd.sh --service-user X # override service user
#
# Idempotent. Re-running with the same flags re-renders the templates
# and reloads systemd; nothing is removed unless --uninstall is passed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE_DIR="${REPO_ROOT}/core/console/systemd"

ACTION="install"
USER_MODE=0
NO_AUTOSTART=0
SERVICE_USER=""

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall)      ACTION="uninstall"; shift ;;
    --user-mode)      USER_MODE=1; shift ;;
    --no-autostart)   NO_AUTOSTART=1; shift ;;
    --service-user)   SERVICE_USER="$2"; shift 2 ;;
    --service-user=*) SERVICE_USER="${1#--service-user=}"; shift ;;
    -h|--help)
      sed -n '1,30p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ "${USER_MODE}" -eq 1 ]; then
  SYSTEMD_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
  AUTOSTART_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/autostart"
  SYSTEMCTL="systemctl --user"
  [ -z "${SERVICE_USER}" ] && SERVICE_USER="$(id -un)"
  SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
else
  if [ "$(id -u)" -ne 0 ]; then
    echo "this script needs root for system-wide install; re-run with sudo or pass --user-mode" >&2
    exit 1
  fi
  SYSTEMD_DIR="/etc/systemd/system"
  AUTOSTART_DIR="/etc/xdg/autostart"
  SYSTEMCTL="systemctl"
  [ -z "${SERVICE_USER}" ] && SERVICE_USER="corvin"
  SERVICE_GROUP="${SERVICE_USER}"
fi

UNIT_NAME="corvin-operator-ui.service"
WATCHDOG_NAME="corvin-operator-ui-watchdog.service"
WATCHDOG_TIMER_NAME="corvin-operator-ui-watchdog.timer"
DESKTOP_NAME="corvin-operator-ui.desktop"

log() { printf '\033[1;36m[install-systemd]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install-systemd]\033[0m WARN: %s\n' "$*" >&2; }
die() { printf '\033[1;31m[install-systemd]\033[0m %s\n' "$*" >&2; exit 1; }

if [ "${ACTION}" = "uninstall" ]; then
  log "stopping + disabling units"
  ${SYSTEMCTL} disable --now "${UNIT_NAME}"          2>/dev/null || true
  ${SYSTEMCTL} disable --now "${WATCHDOG_TIMER_NAME}" 2>/dev/null || true
  log "removing unit files + desktop entry"
  rm -f "${SYSTEMD_DIR}/${UNIT_NAME}" \
        "${SYSTEMD_DIR}/${WATCHDOG_NAME}" \
        "${SYSTEMD_DIR}/${WATCHDOG_TIMER_NAME}" \
        "${AUTOSTART_DIR}/${DESKTOP_NAME}"
  ${SYSTEMCTL} daemon-reload || true
  log "done. (Service user '${SERVICE_USER}' and /opt/corvin/ are NOT removed.)"
  exit 0
fi

# ── install ──────────────────────────────────────────────────────────

if [ "${USER_MODE}" -eq 0 ]; then
  if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    log "creating service user '${SERVICE_USER}'"
    useradd --system --home-dir /opt/corvin --shell /usr/sbin/nologin \
            --comment "Corvin operator UI" "${SERVICE_USER}"
  fi
  log "ensuring /opt/corvin and /var/log/corvin"
  install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 /opt/corvin
  install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0755 /var/log/corvin
  install -d -o root -g root -m 0755 /etc/corvin
  # Touch the env file so EnvironmentFile=-... doesn't complain.
  [ -e /etc/corvin/operator-ui.env ] || \
    install -o root -g root -m 0644 /dev/null /etc/corvin/operator-ui.env
fi

if [ ! -d "${REPO_ROOT}/core/console/.venv" ]; then
  warn "no venv at ${REPO_ROOT}/core/console/.venv — run bootstrap.sh first"
fi

log "rendering unit files into ${SYSTEMD_DIR}"
install -d -m 0755 "${SYSTEMD_DIR}"

# In user-mode we DON'T set User=/Group= (systemd-user units forbid it)
# so we strip those two lines from the template.
render_unit () {
  local src="$1"
  local dst="$2"
  if [ "${USER_MODE}" -eq 1 ]; then
    sed -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
        -e "/^User=/d" \
        -e "/^Group=/d" \
        "${src}" >"${dst}"
  else
    sed -e "s|__REPO_ROOT__|${REPO_ROOT}|g" \
        -e "s|__SERVICE_USER__|${SERVICE_USER}|g" \
        -e "s|__SERVICE_GROUP__|${SERVICE_GROUP}|g" \
        "${src}" >"${dst}"
  fi
  chmod 0644 "${dst}"
}

render_unit "${TEMPLATE_DIR}/corvin-operator-ui.service.in"           "${SYSTEMD_DIR}/${UNIT_NAME}"
render_unit "${TEMPLATE_DIR}/corvin-operator-ui-watchdog.service.in"  "${SYSTEMD_DIR}/${WATCHDOG_NAME}"
install -m 0644 "${TEMPLATE_DIR}/corvin-operator-ui-watchdog.timer"   "${SYSTEMD_DIR}/${WATCHDOG_TIMER_NAME}"

if [ "${NO_AUTOSTART}" -eq 0 ]; then
  log "installing browser auto-open at ${AUTOSTART_DIR}/${DESKTOP_NAME}"
  install -d -m 0755 "${AUTOSTART_DIR}"
  install -m 0644 "${TEMPLATE_DIR}/corvin-operator-ui.desktop.in" \
                  "${AUTOSTART_DIR}/${DESKTOP_NAME}"
else
  log "skipping browser auto-open (--no-autostart)"
fi

log "reloading systemd + enabling units"
${SYSTEMCTL} daemon-reload
${SYSTEMCTL} enable --now "${UNIT_NAME}"
${SYSTEMCTL} enable --now "${WATCHDOG_TIMER_NAME}" || warn "watchdog timer enable failed (non-fatal)"

log "done."
log "  • systemd status: ${SYSTEMCTL} status ${UNIT_NAME} --no-pager"
log "  • health probe:   curl -s http://127.0.0.1:8765/healthz"
log "  • console URL:    http://127.0.0.1:8765/console/"
if [ "${NO_AUTOSTART}" -eq 0 ]; then
  log "  • Browser opens on next desktop login (set CORVIN_NO_AUTOSTART=1 to suppress)."
fi
