#!/usr/bin/env bash
# safe-restart.sh — restart user services WITHOUT killing the work they are doing.
#
# `systemctl --user restart <unit>` ends the unit's whole control group
# (KillMode=control-group): every claude turn running inside it dies mid-task and the
# user gets no closing message and no voice summary. Measured 2026-10-08: corvin-webui
# was restarted five times in one day and the boot reaper marked the then-running
# console tasks `orphaned_on_restart` in the same second (22:25:40).
#
# This waits until no `claude -p` turn is running in the unit's cgroup (idle on two
# consecutive polls, so the gap between two turns is not mistaken for idleness), then
# restarts it. It never kills a running turn: if the unit is still busy when --wait
# runs out it exits 3 and changes nothing.
#
# Run it detached when the caller itself lives inside one of the units (a console chat
# restarting corvin-webui would wait for its own turn forever):
#   systemd-run --user --unit=safe-restart --collect \
#       scripts/safe-restart.sh --wait 5400 corvin-voice-bridge-adapter corvin-webui
#
# Usage: safe-restart.sh [--wait SECONDS] [--poll SECONDS] [--dry-run] UNIT...
set -u

WAIT=1800
POLL=10
DRY=0
UNITS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --wait) WAIT="$2"; shift 2 ;;
    --poll) POLL="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) UNITS+=("${1%.service}"); shift ;;
  esac
done
[ "${#UNITS[@]}" -gt 0 ] || { echo "usage: $0 [--wait S] [--poll S] [--dry-run] UNIT..." >&2; exit 2; }

log() { printf '%s safe-restart: %s\n' "$(date +%H:%M:%S)" "$*"; }

# PIDs of running claude turns inside the unit's control group (one-shot `claude -p`).
busy_pids() {
  local unit="$1" cg procs pid cmd
  cg="$(systemctl --user show "${unit}.service" -p ControlGroup --value 2>/dev/null)"
  [ -n "$cg" ] || return 0
  procs="/sys/fs/cgroup${cg}/cgroup.procs"
  [ -r "$procs" ] || return 0
  while read -r pid; do
    [ -r "/proc/$pid/cmdline" ] || continue
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
    case "$cmd" in
      */claude\ *-p\ *|*/claude\ -p\ *|*/claude\ --resume\ *-p\ *|claude\ *\ -p\ *) echo "$pid" ;;
    esac
  done < "$procs"
}

rc=0
for unit in "${UNITS[@]}"; do
  if ! systemctl --user cat "${unit}.service" >/dev/null 2>&1; then
    log "$unit: no such user unit — skipped"; rc=2; continue
  fi
  deadline=$(( $(date +%s) + WAIT ))
  quiet=0
  while :; do
    pids="$(busy_pids "$unit" | tr '\n' ' ')"
    if [ -z "${pids// /}" ]; then
      quiet=$(( quiet + 1 ))
      [ "$quiet" -ge 2 ] && break
    else
      quiet=0
      log "$unit: busy (claude pid(s): ${pids}) — waiting"
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "$unit: still busy after ${WAIT}s — NOT restarted (nothing was killed)"
      rc=3; continue 2
    fi
    sleep "$POLL"
  done
  if [ "$DRY" -eq 1 ]; then
    log "$unit: idle — would restart (dry run)"
  else
    log "$unit: idle — restarting"
    systemctl --user restart "${unit}.service" && log "$unit: restarted" || { log "$unit: restart FAILED"; rc=1; }
  fi
done
exit "$rc"
