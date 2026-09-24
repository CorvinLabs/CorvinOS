#!/usr/bin/env bash
# uninstall.sh — remove CorvinOS completely (Linux, macOS, WSL, Git-Bash).
#
# Usage:
#   bash uninstall.sh                 # asks once, backs up, removes everything
#   bash uninstall.sh --yes           # unattended (no question)
#   bash uninstall.sh --no-backup     # skip the safety backup
#   bash uninstall.sh --keep-data     # remove the software, keep data + secrets
#   bash uninstall.sh --dry-run       # show what would be removed, change nothing
#   bash uninstall.sh --verify-only   # only report leftovers (exit 1 if any)
#
# Self-contained: it needs nothing from the repository and nothing from the
# installed Python package, so it still works when the install is broken
# (half-finished install, deleted venv, missing uv). Order matters and is
# deliberate:
#   1. backup     — one tarball of every data/secret dir (mode 600), BEFORE
#                   anything is touched. The audit chain is the GDPR Art. 30
#                   record; it is archived, never silently discarded.
#   2. stop       — watchdog first (it would restart the console), then every
#                   service unit / LaunchAgent, then any stray process.
#   3. unregister — unit files, LaunchAgents, Claude Code plugin + marketplace.
#   4. remove     — the uv tool, its shims, the installer-managed source tree
#                   and the data directories.
#   5. verify     — re-scan for every artefact above; exit 1 if anything is
#                   left, naming it. "Uninstalled" is a measured claim.
#
# A developer checkout (a git repo the operator cloned) is never deleted —
# only its generated state (.corvin/). The source tree the installer itself
# created (marker file .corvin-managed) is removed.
set -u

YES=0; BACKUP=1; KEEP_DATA=0; DRY=0; VERIFY_ONLY=0
BACKUP_DIR="${CORVIN_BACKUP_DIR:-$HOME}"
PORT="${CORVIN_CONSOLE_PORT:-8765}"
while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes|--force|--purge) YES=1 ;;
        --no-backup) BACKUP=0 ;;
        --keep-data) KEEP_DATA=1 ;;
        --backup-dir) shift; BACKUP_DIR="${1:?--backup-dir needs a path}" ;;
        --dry-run) DRY=1; YES=1 ;;
        --verify-only) VERIFY_ONLY=1 ;;
        -h|--help) sed -n '2,12p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown argument: $1 (see --help)" >&2; exit 64 ;;
    esac
    shift
done

if [ -t 1 ]; then
    _b() { printf '\033[1m%s\033[0m' "$*"; }; _g() { printf '\033[32m%s\033[0m' "$*"; }
    _y() { printf '\033[33m%s\033[0m' "$*"; }; _r() { printf '\033[31m%s\033[0m' "$*"; }
else
    _b() { printf '%s' "$*"; }; _g() { printf '%s' "$*"; }; _y() { printf '%s' "$*"; }; _r() { printf '%s' "$*"; }
fi
ok()   { if [ "$DRY" = 1 ]; then printf '  ~ would: %s\n' "$*"; else printf '  %s %s\n' "$(_g '✓')" "$*"; fi; }
warn() { printf '  %s %s\n' "$(_y '⚠')" "$*"; }
info() { printf '  %s\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then printf '  [dry-run] %s\n' "$*" >&2; else "$@"; fi; }

OS="$(uname -s 2>/dev/null || echo unknown)"
case "$OS" in
    Linux*)  PLATFORM=linux ;;
    Darwin*) PLATFORM=macos ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
    *) PLATFORM=other ;;
esac
if [ "$PLATFORM" = windows ]; then
    echo "On Windows run the PowerShell uninstaller instead:" >&2
    echo "  powershell -ExecutionPolicy Bypass -File uninstall.ps1" >&2
    exit 2
fi

# ── Locate everything ────────────────────────────────────────────────────────
XDG_CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
VOICE_DIR="${VOICE_CONFIG_DIR:-$XDG_CFG/corvin-voice}"
UV_TOOL_DIR="${UV_TOOL_DIR:-}"
if [ -z "$UV_TOOL_DIR" ] && command -v uv >/dev/null 2>&1; then
    UV_TOOL_DIR="$(uv tool dir 2>/dev/null || true)"
fi
[ -n "$UV_TOOL_DIR" ] || UV_TOOL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools"
TOOL_ENV="$UV_TOOL_DIR/corvinos"
BIN_DIR="${UV_TOOL_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
MANAGED_SRC="${CORVIN_SRC_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/corvinos/src}"

# Source trees an install runs from: the editable path in the uv receipt and
# the installer-managed tree. NOT the directory this script sits in — running
# the repo's copy of this script (a test, a curious developer) must never
# reach into a checkout that no install points at.
SRC_DIRS=""
_add_src() {
    [ -n "$1" ] && [ -f "$1/pyproject.toml" ] || return 0
    case " $SRC_DIRS " in *" $1 "*) return 0 ;; esac
    SRC_DIRS="$SRC_DIRS $1"
}
if [ -f "$TOOL_ENV/uv-receipt.toml" ]; then
    _add_src "$(sed -n 's/.*editable = "\([^"]*\)".*/\1/p' "$TOOL_ENV/uv-receipt.toml" | head -1)"
fi
_add_src "$MANAGED_SRC"

# Deletion is confined to $HOME. The one exception is an explicit CORVIN_HOME
# (an operator who put the data elsewhere said so themselves).
_HOME_REAL="$(cd "$HOME" 2>/dev/null && pwd -P || echo "$HOME")"
_under_home() {
    _p="$(cd "$1" 2>/dev/null && pwd -P || echo "$1")"
    case "$_p" in "$_HOME_REAL"/*) return 0 ;; *) return 1 ;; esac
}
DATA_DIRS=""
_add_data() {
    [ -n "$1" ] && [ -e "$1" ] || return 0
    case " $DATA_DIRS " in *" $1 "*) return 0 ;; esac
    if ! _under_home "$1" && [ "$1" != "${CORVIN_HOME:-}" ]; then
        echo "  (skipping $1 — outside \$HOME; set CORVIN_HOME to include it)" >&2
        return 0
    fi
    DATA_DIRS="$DATA_DIRS $1"
}
_add_data "${CORVIN_HOME:-}"
_add_data "$HOME/.corvin"
for _s in $SRC_DIRS; do _add_data "$_s/.corvin"; done
_add_data "$VOICE_DIR"
_add_data "$XDG_CFG/claude-cowork"

SYSTEMD_USER_DIR="$XDG_CFG/systemd/user"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

_units() {
    [ -d "$SYSTEMD_USER_DIR" ] || return 0
    ls "$SYSTEMD_USER_DIR" 2>/dev/null | grep -E '^corvin-.*\.(service|timer|path|socket)$' || true
}
_plists() { ls "$LAUNCH_DIR"/com.corvin.*.plist 2>/dev/null || true; }
# Process patterns: specific enough never to match an editor, a shell in the
# repo, or this script. Kept in one place so stop + verify agree.
# Script paths only count when an interpreter runs them: an editor with
# adapter.py or daemon.js open must never be killed.
_PROC_RE='corvin_gateway\.app|corvinos-serve|corvin-serve|corvin-service|python[0-9.]*[^ ]* [^ ]*bridges/shared/adapter\.py|node[^ ]* [^ ]*corvin_operator/bridges/[a-z]+/daemon\.js|uv/tools/corvinos/bin/python|corvin-watchdog|watchdog-health-check\.sh'
_procs() {
    ps -eo pid=,args= 2>/dev/null | grep -E "$_PROC_RE" | grep -v -E 'grep|uninstall\.sh' \
        | awk -v me="$$" '$1 != me {print $1}' || true
}
_port_busy() {
    if command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null | grep -qE "[:.]$PORT[[:space:]]"
    elif command -v lsof >/dev/null 2>&1; then lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
    elif command -v netstat >/dev/null 2>&1; then netstat -an 2>/dev/null | grep -E "LISTEN" | grep -qE "[:.]$PORT[[:space:]]"
    else return 1; fi
}
_shims() {
    [ -d "$BIN_DIR" ] || return 0
    for f in "$BIN_DIR"/corvin "$BIN_DIR"/corvin-* "$BIN_DIR"/corvinos "$BIN_DIR"/corvinos-*; do
        [ -e "$f" ] || [ -L "$f" ] || continue
        # Only shims that belong to the corvinos tool (symlink into it, or a
        # script that execs its interpreter). Never a user's own `corvin-foo`.
        if [ -L "$f" ]; then
            case "$(readlink "$f")" in *uv/tools/corvinos*|*/corvinos/bin/*) echo "$f" ;; esac
        elif grep -q 'corvinos' "$f" 2>/dev/null; then echo "$f"; fi
    done
}

# ── Verify-only / final report ──────────────────────────────────────────────
LEFT=0
_left() { printf '  %s %s\n' "$(_r '✗')" "$*"; LEFT=$((LEFT + 1)); }
verify() {
    LEFT=0
    for u in $(_units); do _left "service unit: $SYSTEMD_USER_DIR/$u"; done
    if [ -d "$SYSTEMD_USER_DIR" ]; then
        for l in "$SYSTEMD_USER_DIR"/*.wants/corvin-*; do [ -e "$l" ] || [ -L "$l" ] && _left "unit link: $l"; done
    fi
    for p in $(_plists); do _left "LaunchAgent: $p"; done
    for p in $(_procs); do _left "process $p: $(ps -o args= -p "$p" 2>/dev/null | cut -c1-100)"; done
    _port_busy && _left "something still listens on TCP $PORT"
    [ -d "$TOOL_ENV" ] && _left "uv tool env: $TOOL_ENV"
    for s in $(_shims); do _left "command shim: $s"; done
    if [ "$KEEP_DATA" != 1 ]; then
        for d in $DATA_DIRS; do [ -e "$d" ] && _left "data dir: $d"; done
    fi
    [ -f "$MANAGED_SRC/.corvin-managed" ] && _left "managed source tree: $MANAGED_SRC"
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        for c in $(docker ps -aq --filter label=app=corvinOS 2>/dev/null); do _left "Docker container: $c"; done
    fi
    return 0
}

if [ "$VERIFY_ONLY" = 1 ]; then
    printf '\n%s\n' "$(_b 'CorvinOS leftover scan')"
    verify
    if [ "$LEFT" -eq 0 ]; then ok "nothing left — CorvinOS is not installed"; exit 0; fi
    printf '  %s artefact(s) remain\n' "$LEFT"; exit 1
fi

# ── Confirm ──────────────────────────────────────────────────────────────────
printf '\n%s\n\n' "$(_b 'CorvinOS uninstaller')"
info "Platform      : $PLATFORM"
info "Data          : ${DATA_DIRS:- (none found)}"
info "uv tool env   : $([ -d "$TOOL_ENV" ] && echo "$TOOL_ENV" || echo '(not installed)')"
info "Source trees  : ${SRC_DIRS:- (none)}"
[ "$KEEP_DATA" = 1 ] && info "Mode          : keep data (software only)"
echo ""
if [ "$YES" != 1 ]; then
    if [ -t 0 ]; then
        printf '  Remove CorvinOS completely%s? [y/N] ' "$([ "$BACKUP" = 1 ] && echo ' (a backup is taken first)')"
        read -r _a || _a=""
        case "$_a" in y|Y|yes|YES|j|J|ja) ;; *) echo "  Aborted — nothing changed."; exit 0 ;; esac
    else
        echo "  Not a terminal and --yes not given — refusing to uninstall unattended." >&2
        echo "  Re-run with: bash uninstall.sh --yes" >&2
        exit 2
    fi
fi


# ── 1. Stop ─────────────────────────────────────────────────────────────────
# Stop BEFORE the backup: a live console appends to the audit chain while tar
# reads it, which yields a torn last record in the archive.
printf '\n%s\n' "$(_b '[1/5] Stopping services and processes')"
STOPPED_UNITS=""; STOPPED_PLISTS=""
if command -v systemctl >/dev/null 2>&1 && [ -d "$SYSTEMD_USER_DIR" ]; then
    # Watchdog first: it restarts the console every 30 s while it lives.
    for u in $(_units | grep watchdog) $(_units | grep -v watchdog); do
        if systemctl --user is-active --quiet "$u" 2>/dev/null; then
            STOPPED_UNITS="$STOPPED_UNITS $u"
        fi
        run systemctl --user stop "$u" >/dev/null 2>&1 || true
        ok "stopped $u"
    done
fi
if [ "$PLATFORM" = macos ]; then
    for p in $(_plists); do
        run launchctl bootout "gui/$(id -u)" "$p" >/dev/null 2>&1 || run launchctl unload "$p" >/dev/null 2>&1 || true
        STOPPED_PLISTS="$STOPPED_PLISTS $p"
        ok "stopped $(basename "$p")"
    done
fi
# Root-level (always-on, "Stufe 2") service: only removable with root.
for sysu in /etc/systemd/system/corvin-*.service /Library/LaunchDaemons/com.corvin.*.plist; do
    [ -e "$sysu" ] || continue
    if [ "$(id -u)" = 0 ] || sudo -n true 2>/dev/null; then
        _sudo=""; [ "$(id -u)" = 0 ] || _sudo="sudo -n"
        case "$sysu" in
            /etc/*) run $_sudo systemctl disable --now "$(basename "$sysu")" >/dev/null 2>&1 || true ;;
            *)      run $_sudo launchctl bootout system "$sysu" >/dev/null 2>&1 || true ;;
        esac
        run $_sudo rm -f "$sysu" && ok "removed system service $sysu"
    else
        warn "system service $sysu needs root: sudo corvin-service uninstall  (or: sudo rm $sysu)"
    fi
done
[ -d /etc/systemd/system ] && command -v systemctl >/dev/null 2>&1 && { sudo -n systemctl daemon-reload >/dev/null 2>&1 || true; }

_stop_procs() {
    _pids="$(_procs)"
    [ -n "$_pids" ] || return 0
    run kill $_pids 2>/dev/null || true
    [ "$DRY" = 1 ] && return 0
    for _i in 1 2 3 4 5 6 7 8 9 10; do
        [ -n "$(_procs)" ] || break
        sleep 1
    done
    _pids="$(_procs)"
    [ -n "$_pids" ] && kill -9 $_pids 2>/dev/null || true
}
_stop_procs
if [ "$DRY" = 1 ]; then :
elif [ -z "$(_procs)" ]; then ok "no CorvinOS process running"
else warn "some processes survived: $(_procs | tr '\n' ' ')"; fi

# Undo step 1 — used only when the backup fails, so an aborted uninstall
# leaves the operator with a RUNNING CorvinOS, not a stopped one.
_restart_stopped() {
    for u in $STOPPED_UNITS; do systemctl --user start "$u" >/dev/null 2>&1 || true; done
    for p in $STOPPED_PLISTS; do launchctl load "$p" >/dev/null 2>&1 || true; done
    [ -n "$STOPPED_UNITS$STOPPED_PLISTS" ] && echo "  Services restarted — CorvinOS is running as before." >&2
    return 0
}

# ── 2. Backup ────────────────────────────────────────────────────────────────
BACKUP_FILE=""
if [ "$BACKUP" = 1 ] && [ "$KEEP_DATA" != 1 ] && [ -n "$DATA_DIRS" ]; then
    printf '\n%s\n' "$(_b '[2/5] Backup')"
    BACKUP_FILE="$BACKUP_DIR/corvin-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    # Absolute paths are stored relative to / so a restore is `tar -xzf F -C /`.
    _rel=""
    for d in $DATA_DIRS; do _rel="$_rel ${d#/}"; done
    for u in $(_units); do _rel="$_rel ${SYSTEMD_USER_DIR#/}/$u"; done
    for p in $(_plists); do _rel="$_rel ${p#/}"; done
    if [ "$DRY" = 1 ]; then
        info "[dry-run] would write $BACKUP_FILE"
    else
        mkdir -p "$BACKUP_DIR" 2>/dev/null || true
        _terr="${TMPDIR:-/tmp}/corvin-uninstall-tar.$$"
        # Re-downloadable runtimes are excluded; everything a user cannot get
        # back (secrets, pairing, audit chain, sessions, models) is kept.
        # LC_ALL=C: the warning text below is matched, and tar localises it.
        # shellcheck disable=SC2086
        if ( umask 077; LC_ALL=C tar -czf "$BACKUP_FILE" -C / \
                --exclude='*/node_modules' --exclude='*/.corvin/node' --exclude='*/venv' \
                --exclude='*/__pycache__' --exclude='*.sock' $_rel ) 2>"$_terr" \
           || { [ -s "$BACKUP_FILE" ] && ! grep -vqiE 'file changed as we read|socket ignored|removing leading' "$_terr"; }; then
            chmod 600 "$BACKUP_FILE" 2>/dev/null || true
            ok "backup: $BACKUP_FILE ($(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1))"
            info "   restore later with: tar -xzf '$BACKUP_FILE' -C /"
            rm -f "$_terr"
        else
            cat "$_terr" >&2; rm -f "$_terr" "$BACKUP_FILE"
            echo "  $(_r 'Backup failed — nothing was removed.') Free space in $BACKUP_DIR, pass --backup-dir, or --no-backup." >&2
            _restart_stopped
            exit 1
        fi
    fi
else
    printf '\n%s\n' "$(_b '[2/5] Backup — skipped')"
fi

# ── 3. Unregister ────────────────────────────────────────────────────────────
printf '\n%s\n' "$(_b '[3/5] Removing autostart entries and integrations')"
if [ -d "$SYSTEMD_USER_DIR" ]; then
    for u in $(_units); do
        command -v systemctl >/dev/null 2>&1 && { run systemctl --user disable "$u" >/dev/null 2>&1 || true; }
        run rm -f "$SYSTEMD_USER_DIR/$u" && ok "removed $u"
    done
    for l in "$SYSTEMD_USER_DIR"/*.wants/corvin-*; do [ -e "$l" ] || [ -L "$l" ] && run rm -f "$l"; done
    command -v systemctl >/dev/null 2>&1 && { run systemctl --user daemon-reload >/dev/null 2>&1 || true; run systemctl --user reset-failed >/dev/null 2>&1 || true; }
fi
for p in $(_plists); do run rm -f "$p" && ok "removed $(basename "$p")"; done
# Claude Code integration (voice + cowork plugins and their marketplace).
CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
[ -z "$CLAUDE_BIN" ] && [ -x "$HOME/.local/bin/claude" ] && CLAUDE_BIN="$HOME/.local/bin/claude"
if [ -n "$CLAUDE_BIN" ]; then
    for pl in voice@corvin-voice-local cowork@corvin-voice-local; do
        run "$CLAUDE_BIN" plugin uninstall "$pl" >/dev/null 2>&1 && ok "Claude Code plugin removed: $pl"
    done
    run "$CLAUDE_BIN" plugin marketplace remove corvin-voice-local >/dev/null 2>&1 && ok "Claude Code marketplace removed: corvin-voice-local"
fi
[ -d "$HOME/.claude/plugins/cache/corvin-voice-local" ] && run rm -rf "$HOME/.claude/plugins/cache/corvin-voice-local"
[ -d "$HOME/.claude/plugins/marketplaces/corvin-voice-local" ] && run rm -rf "$HOME/.claude/plugins/marketplaces/corvin-voice-local"
# Docker deployment (containers labelled by the compose file) — only ours.
# ADR-0868: a container's CORVIN_HOME (audit chain = GDPR Art. 30 record) lives
# in a volume the host backup above cannot see. Export it from every labelled
# container FIRST; volumes are destroyed only when every export succeeded
# (or the operator passed --no-backup). Fail-closed: a failed export keeps them.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    _c="$(docker ps -aq --filter label=app=corvinOS 2>/dev/null)"
    _export_ok=1
    if [ -n "$_c" ] && [ "$KEEP_DATA" != 1 ] && [ "$BACKUP" = 1 ]; then
        _dx="$BACKUP_DIR/corvin-backup-docker-$(date +%Y%m%d-%H%M%S)"
        run mkdir -p "$_dx" && { [ "$DRY" = 1 ] || chmod 700 "$_dx"; }
        for _id in $_c; do
            _name="$(docker inspect -f '{{.Name}}' "$_id" 2>/dev/null | tr -d '/')"
            if run docker cp "$_id:/root/.corvin" "$_dx/${_name:-$_id}" >/dev/null 2>&1; then
                ok "exported container data: ${_name:-$_id} → $_dx"
            else
                _export_ok=0; warn "could not export data from container ${_name:-$_id}"
            fi
        done
    fi
    [ -n "$_c" ] && run docker rm -f $_c >/dev/null 2>&1 && ok "removed Docker containers"
    if [ "$KEEP_DATA" != 1 ]; then
        _v="$(docker volume ls -q --filter label=app=corvinOS 2>/dev/null)"
        if [ -n "$_v" ] && [ "$_export_ok" = 1 ]; then
            run docker volume rm $_v >/dev/null 2>&1 && ok "removed Docker volumes"
        elif [ -n "$_v" ]; then
            warn "Docker volumes KEPT (export failed): $(echo $_v) — remove with: docker volume rm $(echo $_v)"
        fi
    fi
fi

# ── 4. Remove ───────────────────────────────────────────────────────────────
printf '\n%s\n' "$(_b '[4/5] Removing software and data')"
if command -v uv >/dev/null 2>&1 && [ -d "$TOOL_ENV" ]; then
    run uv tool uninstall corvinos >/dev/null 2>&1 && ok "uv tool corvinos uninstalled"
fi
# A broken receipt makes `uv tool uninstall` fail — remove the env directly.
[ -d "$TOOL_ENV" ] && run rm -rf "$TOOL_ENV" && ok "removed $TOOL_ENV"
for s in $(_shims); do run rm -f "$s"; done
[ -z "$(_shims)" ] && ok "command shims removed"

if [ "$KEEP_DATA" != 1 ]; then
    for d in $DATA_DIRS; do
        run rm -rf "$d" && ok "removed $d"
        # Busy files (a process that respawned) — one retry after a stop.
        if [ "$DRY" != 1 ] && [ -e "$d" ]; then _stop_procs; rm -rf "$d" 2>/dev/null; fi
        [ "$DRY" != 1 ] && [ -e "$d" ] && warn "could not fully remove $d"
    done
fi
if [ -f "$MANAGED_SRC/.corvin-managed" ]; then
    if [ "$KEEP_DATA" = 1 ] && [ -e "$MANAGED_SRC/.corvin" ]; then
        # The managed tree also holds the install's data (<src>/.corvin):
        # --keep-data removes the code around it, never the data itself.
        for _e in "$MANAGED_SRC"/* "$MANAGED_SRC"/.[!.]*; do
            [ -e "$_e" ] || [ -L "$_e" ] || continue
            [ "$(basename "$_e")" = .corvin ] && continue
            run rm -rf "$_e"
        done
        ok "removed installer-managed source (kept its data: $MANAGED_SRC/.corvin)"
    else
        run rm -rf "$MANAGED_SRC" && ok "removed installer-managed source $MANAGED_SRC"
        rmdir "$(dirname "$MANAGED_SRC")" 2>/dev/null || true
    fi
fi
for s in $SRC_DIRS; do
    [ "$s" = "$MANAGED_SRC" ] && continue
    info "developer checkout kept: $s (only its .corvin/ state was removed)"
done
rm -f "${TMPDIR:-/tmp}"/corvinos-install.log "${TMPDIR:-/tmp}"/corvinos-update.log 2>/dev/null || true

# ── 5. Verify ────────────────────────────────────────────────────────────────
printf '\n%s\n' "$(_b '[5/5] Verifying')"
if [ "$DRY" = 1 ]; then
    info "[dry-run] nothing was changed"
    exit 0
fi
verify
echo ""
if [ "$LEFT" -eq 0 ]; then
    printf '%s\n' "$(_g "$(_b 'CorvinOS was removed completely.')")"
    [ -n "$BACKUP_FILE" ] && printf '  Backup: %s\n' "$BACKUP_FILE"
    echo "  Not removed (shared with other software): uv, Claude Code, the Playwright browser cache."
    exit 0
fi
printf '%s %s artefact(s) could not be removed — see the list above.\n' "$(_r 'Incomplete:')" "$LEFT"
[ -n "$BACKUP_FILE" ] && printf '  Backup: %s\n' "$BACKUP_FILE"
exit 1
