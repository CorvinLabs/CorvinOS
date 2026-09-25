#!/bin/sh
# update.sh — update CorvinOS to the latest main, rebuild the console, restart,
# prove the new build is what the browser gets, roll back if it is not.
# Linux, macOS, WSL.  (Windows: update.ps1)
#
# Usage:
#   sh update.sh                  # update to origin/main, rebuild, restart, verify
#   sh update.sh --rebuild-only   # no download: reinstall + rebuild + restart current code
#   sh update.sh --no-restart     # stage everything, leave the running console alone
#   sh update.sh --no-rollback    # keep the new code even if verification fails
#   curl -fsSL https://raw.githubusercontent.com/CorvinLabs/CorvinOS/main/update.sh | sh
#
# Self-contained: needs only sh + curl/wget. It bootstraps uv and Node.js when
# they are missing, repairs a broken tool venv, and when no install exists at
# all it hands over to install.sh (so "update" on a wiped machine installs).
#
# Which source gets updated:
#   * installer-managed tree (has .corvin-managed): reset to origin/main —
#     it is ours, local edits are not preserved.
#   * a developer checkout on main: fast-forward; if main was rewritten
#     (force-push), the old HEAD is kept as branch corvin-update-backup-<ts>
#     and local changes as a git stash before resetting — nothing is lost.
#   * a developer checkout on another branch: code left alone (warned),
#     but deps, frontend and services are still refreshed.
#   * a PyPI install: PyPI lags main, so it is converted to a managed tree.
#
# Exit: 0 updated + verified · 1 failed, rolled back (old version running)
#       2 failed and rollback failed too (details printed) · 3 lock held
set -u

# This file is itself replaced by the update (git reset / tarball swap), and
# sh reads a script incrementally — run from a private copy so the running
# code cannot change underneath us. (Piped `curl | sh` has no file: no-op.)
if [ -z "${CORVIN_UPDATE_REEXEC:-}" ] && [ -f "$0" ]; then
    _self="$(mktemp "${TMPDIR:-/tmp}/corvinos-update.XXXXXX")" && cp "$0" "$_self" \
        && CORVIN_UPDATE_REEXEC="$_self" exec sh "$_self" "$@"
fi
[ -n "${CORVIN_UPDATE_REEXEC:-}" ] && [ -f "$CORVIN_UPDATE_REEXEC" ] && rm -f "$CORVIN_UPDATE_REEXEC"

BRANCH="${CORVIN_BRANCH:-main}"
REPO_URL="${CORVIN_REPO_URL:-https://github.com/CorvinLabs/CorvinOS}"
PORT="${CORVIN_CONSOLE_PORT:-8765}"
BASE_URL="http://127.0.0.1:${PORT}"
MANAGED_SRC="${CORVIN_SRC_DIR:-${XDG_DATA_HOME:-${HOME:-}/.local/share}/corvinos/src}"
LOG="${CORVIN_UPDATE_LOG:-${TMPDIR:-/tmp}/corvinos-update.log}"
UV_PIN_VERSION="0.12.9"
UV_INSTALLER_SHA256="222e006c0fe4a0d793031833e469b21df72311f4e3526ffecca0e19e6dfabc32"
REBUILD_ONLY=0; NO_RESTART=0; ROLLBACK=1

while [ $# -gt 0 ]; do
    case "$1" in
        --rebuild-only|--console-only) REBUILD_ONLY=1 ;;
        --no-restart) NO_RESTART=1 ;;
        --no-rollback) ROLLBACK=0 ;;
        --force) ;;  # accepted for compatibility; every run rebuilds
        -h|--help) sed -n '2,14p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
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
ok()   { printf '  %s %s\n' "$(_g '✓')" "$*"; }
warn() { printf '  %s %s\n' "$(_y '⚠')" "$*"; }
step() { printf '\n%s\n' "$(_b "$*")"; }
die()  { printf '  %s %s\n' "$(_r '✗')" "$*" >&2; exit "${2:-1}"; }
log()  { printf '\n=== %s %s ===\n' "$(date '+%H:%M:%S' 2>/dev/null)" "$*" >>"$LOG" 2>/dev/null; }

# _quiet MSG cmd… — run with output to the log, dots while it works.
_quiet() {
    _q_msg="$1"; shift
    printf '  … %s ' "$_q_msg"
    log "$_q_msg"
    "$@" </dev/null >>"$LOG" 2>&1 &
    _q_pid=$!
    while kill -0 "$_q_pid" 2>/dev/null; do printf '.'; sleep 2; done
    if wait "$_q_pid"; then printf ' %s\n' "$(_g '✓')"; return 0; fi
    printf ' %s\n' "$(_r '✗')"; return 1
}

_retry() {  # _retry N cmd… — backoff 2,4,8 s
    _rt_n="$1"; shift; _rt_i=1; _rt_w=2
    while :; do
        "$@" && return 0
        [ "$_rt_i" -ge "$_rt_n" ] && return 1
        echo "  (attempt $_rt_i/$_rt_n failed — retrying in ${_rt_w}s)" >>"$LOG"
        sleep "$_rt_w"; _rt_i=$((_rt_i + 1)); _rt_w=$((_rt_w * 2))
    done
}

_download() {
    if command -v curl >/dev/null 2>&1; then curl -fsSL --connect-timeout 20 --max-time 900 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then wget -q -T 60 -O "$2" "$1"
    else return 1; fi
}

_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
    else openssl dgst -sha256 "$1" 2>/dev/null | awk '{print $NF}'; fi
}

: >"$LOG" 2>/dev/null || LOG=/dev/null
printf '\n%s\n' "$(_b 'CorvinOS updater')"
echo "  log: $LOG"

# Corporate / Citrix proxies re-sign TLS with a company CA only the OS store
# knows — uv honours the system store with this.
export UV_NATIVE_TLS="${UV_NATIVE_TLS:-1}"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# ── Lock (shared with install.sh; the watchdog stands down while it exists) ──
LOCK_DIR="${TMPDIR:-/tmp}/corvinos-setup.lock"
# Same PID-checked lock as install.sh: a dead owner's lock is stale.
_take_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then echo $$ >"$LOCK_DIR/pid"; return 0; fi
    _lk_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -z "$_lk_pid" ]; then sleep 1; _lk_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"; fi
    if [ -z "$_lk_pid" ] || ! kill -0 "$_lk_pid" 2>/dev/null \
       || [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
        rm -rf "$LOCK_DIR"
        mkdir "$LOCK_DIR" 2>/dev/null && echo $$ >"$LOCK_DIR/pid" && return 0
    fi
    return 1
}
_take_lock || die "another CorvinOS install/update is running (lock: $LOCK_DIR, pid $(cat "$LOCK_DIR/pid" 2>/dev/null))" 3
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

# ── 1. Tooling: uv (self-bootstrapping, pinned + checksummed) ───────────────
step "[1/6] Tools"
if ! command -v uv >/dev/null 2>&1; then
    _uvt="$(mktemp "${TMPDIR:-/tmp}/uv-installer.XXXXXX")" || die "mktemp failed"
    _retry 3 _download "https://github.com/astral-sh/uv/releases/download/${UV_PIN_VERSION}/uv-installer.sh" "$_uvt" \
        || die "could not download uv (network/proxy?)"
    [ "$(_sha256 "$_uvt")" = "$UV_INSTALLER_SHA256" ] || { rm -f "$_uvt"; die "uv installer checksum mismatch — refusing to run it"; }
    _quiet "installing uv ${UV_PIN_VERSION}" sh "$_uvt" || die "uv install failed — see $LOG"
    rm -f "$_uvt"
fi
ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

TOOL_ENV="$(uv tool dir 2>/dev/null)/corvinos"
RECEIPT="$TOOL_ENV/uv-receipt.toml"

# ── 2. Locate the install ────────────────────────────────────────────────────
# The tree the always-on console actually imports — and serves dist/ from —
# is named by its unit's PYTHONPATH (…/core/console first). That tree is the
# one to update. Updating any other tree builds a bundle nobody serves, so
# wait_live can never match, and an editable install pointing elsewhere than
# the unit's PYTHONPATH trips the license validator's path check at runtime.
served_tree() {
    _pp=""
    if command -v systemctl >/dev/null 2>&1; then
        _pp="$(systemctl --user show corvin-webui.service -p Environment --value 2>/dev/null \
            | tr ' ' '\n' | sed -n 's/^"\{0,1\}PYTHONPATH=//p' | tr -d '"' | head -1)"
    fi
    if [ -z "$_pp" ] && command -v plutil >/dev/null 2>&1; then
        for _p in "$HOME"/Library/LaunchAgents/com.corvin.*.plist; do
            [ -f "$_p" ] || continue
            _pp="$(plutil -extract EnvironmentVariables.PYTHONPATH raw -o - "$_p" 2>/dev/null)" && [ -n "$_pp" ] && break
        done
    fi
    _first="$(printf '%s' "$_pp" | cut -d: -f1)"
    case "$_first" in */core/console) printf '%s' "${_first%/core/console}" ;; esac
}
SERVED_SRC="$(served_tree)"
SRC=""; KIND=""
if [ -n "$SERVED_SRC" ] && [ -f "$SERVED_SRC/pyproject.toml" ]; then
    SRC="$SERVED_SRC"
elif [ -f "$RECEIPT" ]; then
    SRC="$(sed -n 's/.*editable = "\([^"]*\)".*/\1/p' "$RECEIPT" | head -1)"
fi
if [ -n "$SRC" ] && [ -f "$SRC/pyproject.toml" ]; then
    if [ -f "$SRC/.corvin-managed" ]; then KIND=managed; else KIND=checkout; fi
elif [ -f "$MANAGED_SRC/pyproject.toml" ]; then
    SRC="$MANAGED_SRC"; KIND=managed
elif [ -f "$RECEIPT" ]; then
    SRC="$MANAGED_SRC"; KIND=pypi
else
    # Nothing installed (or the venv is gone): an update cannot restore what is
    # not there — install instead, from main.
    warn "no CorvinOS install found — running the installer instead"
    _inst="$(mktemp "${TMPDIR:-/tmp}/corvinos-install.XXXXXX")" || die "mktemp failed"
    _retry 3 _download "https://raw.githubusercontent.com/CorvinLabs/CorvinOS/${BRANCH}/install.sh" "$_inst" \
        || die "could not download install.sh"
    rm -rf "$LOCK_DIR"; trap - EXIT INT TERM
    exec sh "$_inst"
fi
step "[2/6] Source ($KIND): $SRC"

PREV_REV=""; BACKUP_REF=""
_git() { git -C "$SRC" "$@"; }
if [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
    PREV_REV="$(_git rev-parse HEAD 2>/dev/null || true)"
fi
WEB="$SRC/core/console/corvin_console/web-next"

fetch_managed() {  # managed tree → exactly origin/$BRANCH
    if [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
        _retry 3 _git fetch --depth 1 origin "$BRANCH" >>"$LOG" 2>&1 || return 1
        _git reset --hard -q FETCH_HEAD >>"$LOG" 2>&1 || return 1
    elif [ ! -e "$SRC" ] && command -v git >/dev/null 2>&1; then
        mkdir -p "$(dirname "$SRC")"
        _retry 3 git clone -q --depth 1 --branch "$BRANCH" "$REPO_URL.git" "$SRC.tmp" >>"$LOG" 2>&1 || { rm -rf "$SRC.tmp"; return 1; }
        mv "$SRC.tmp" "$SRC"
    else
        # No git (typical on locked-down desktops): tarball, swapped in with
        # generated state (.corvin/, node_modules) carried across.
        _tgz="$(mktemp "${TMPDIR:-/tmp}/corvinos-src.XXXXXX")" || return 1
        _url="$(printf '%s' "$REPO_URL" | sed 's#^https://github.com/#https://codeload.github.com/#')/tar.gz/refs/heads/$BRANCH"
        _retry 3 _download "$_url" "$_tgz" || { rm -f "$_tgz"; return 1; }
        rm -rf "$SRC.new"; mkdir -p "$SRC.new"
        tar -xzf "$_tgz" -C "$SRC.new" --strip-components=1 || { rm -rf "$_tgz" "$SRC.new"; return 1; }
        rm -f "$_tgz"
        [ -f "$SRC.new/pyproject.toml" ] || { rm -rf "$SRC.new"; return 1; }
        if [ -d "$SRC" ]; then
            for _k in .corvin core/console/corvin_console/web-next/node_modules core/console/corvin_console/web-next/dist; do
                [ -e "$SRC/$_k" ] && mkdir -p "$(dirname "$SRC.new/$_k")" && mv "$SRC/$_k" "$SRC.new/$_k"
            done
            rm -rf "$SRC.prev"; mv "$SRC" "$SRC.prev"
        fi
        mv "$SRC.new" "$SRC" || return 1
    fi
    : >"$SRC/.corvin-managed"
}

fetch_checkout() {  # developer checkout — never lose work
    command -v git >/dev/null 2>&1 || { warn "git not found — code not updated"; return 0; }
    _cur="$(_git symbolic-ref --short -q HEAD || echo DETACHED)"
    _retry 3 _git fetch origin "$BRANCH" >>"$LOG" 2>&1 || return 1
    if [ "$_cur" != "$BRANCH" ]; then
        warn "checkout is on '$_cur', not '$BRANCH' — code left as is (switch branches yourself to update)"
        return 0
    fi
    if [ -n "$(_git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
        _git stash push -q -m "corvin-update autostash $(date +%Y%m%d-%H%M%S)" >>"$LOG" 2>&1 || return 1
        STASHED=1
    fi
    if _git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
        _git merge -q --ff-only "origin/$BRANCH" >>"$LOG" 2>&1 || return 1
    elif _git merge-base --is-ancestor "origin/$BRANCH" HEAD 2>/dev/null; then
        warn "checkout is ahead of origin/$BRANCH (unpushed commits) — kept as is"
    else
        # Diverged: origin was rewritten, or local commits exist. Keep the old
        # HEAD on a branch, then take origin as the truth ("fresh from main").
        BACKUP_REF="corvin-update-backup-$(date +%Y%m%d-%H%M%S)"
        _git branch "$BACKUP_REF" HEAD >>"$LOG" 2>&1 || return 1
        _git reset -q --hard "origin/$BRANCH" >>"$LOG" 2>&1 || return 1
        warn "origin/$BRANCH was rewritten — your previous HEAD is saved as branch $BACKUP_REF"
    fi
    # Put the local changes back (git pull --autostash semantics). A conflict
    # leaves the tree clean and the stash in place — never conflict markers in
    # files the build below would choke on.
    if [ "$STASHED" = 1 ]; then
        if _git stash apply -q >>"$LOG" 2>&1; then
            _git stash drop -q >>"$LOG" 2>&1 || true
            STASHED=0
            ok "local changes re-applied on top of the update"
        else
            _git reset -q --hard HEAD >>"$LOG" 2>&1 || true
            warn "local changes conflict with the update — kept in the stash (git stash list)"
        fi
    fi
}

STASHED=0
if [ "$REBUILD_ONLY" = 1 ]; then
    ok "code unchanged (--rebuild-only)"
else
    case "$KIND" in
        managed|pypi) _quiet "fetching $BRANCH" fetch_managed || die "could not download the update — nothing changed (see $LOG)" ;;
        checkout) fetch_checkout || die "git update failed — nothing changed (see $LOG)" ;;
    esac
    NEW_REV="$(_git rev-parse HEAD 2>/dev/null || echo tarball)"
    if [ -n "$PREV_REV" ] && [ "$PREV_REV" = "$NEW_REV" ]; then
        ok "already at the latest $BRANCH ($(printf '%.8s' "$NEW_REV")) — refreshing anyway"
    else
        ok "$(printf '%.8s' "${PREV_REV:-none}") → $(printf '%.8s' "$NEW_REV")"
        _git log --oneline "${PREV_REV}..HEAD" 2>/dev/null | head -15 | sed 's/^/      /'
    fi
fi

# ── 3. Python package (deps can change with the code) ───────────────────────
step "[3/6] Python package"
_uv_install() { uv tool install --force --editable "${SRC}[browser]"; }
_uv_install_healing() {
    _uv_install && return 0
    echo "retry: removing the tool venv" >>"$LOG"
    uv tool uninstall corvinos >/dev/null 2>&1 || rm -rf "$TOOL_ENV"
    _uv_install && return 0
    echo "retry: clean uv cache" >>"$LOG"
    uv cache clean corvinos >/dev/null 2>&1 || true
    _uv_install
}
PKG_OK=1
_quiet "installing corvinos from $SRC" _uv_install_healing || PKG_OK=0
export PATH="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"):$PATH"
TOOL_PY="$TOOL_ENV/bin/python"

# ── 4. Console frontend: clean build into dist.next, atomic swap ────────────
step "[4/6] Console frontend"
if [ -f "$SRC/scripts/ensure-node.sh" ]; then
    _quiet "Node.js runtime" bash "$SRC/scripts/ensure-node.sh" || warn "local Node.js bootstrap failed — trying the system Node.js"
fi
[ -x "${CORVIN_HOME:-$HOME/.corvin}/node/bin/npm" ] && export PATH="${CORVIN_HOME:-$HOME/.corvin}/node/bin:$PATH"

_npm_deps() {
    cd "$WEB" || return 1
    if [ -f package-lock.json ]; then
        npm ci --no-audit --no-fund && return 0
        # A half-extracted node_modules (killed run, AV lock) breaks ci — start clean.
        rm -rf node_modules
        npm ci --no-audit --no-fund && return 0
    fi
    npm install --no-audit --no-fund
}
_build() {
    cd "$WEB" || return 1
    rm -rf node_modules/.vite dist.next .tsc-failed
    if ./node_modules/.bin/tsc -b >>"$LOG" 2>&1; then :; else
        # A type error in some panel must not keep every user on the old
        # version: vite (esbuild) still emits a working bundle. Logged loudly.
        echo "WARNING: tsc -b reported type errors (above) — building without the type gate" >>"$LOG"
        : >.tsc-failed  # a file, not a variable: _quiet runs this in a subshell
    fi
    ./node_modules/.bin/vite build --outDir dist.next && [ -f dist.next/index.html ]
}
BUILD_OK=0
if [ ! -f "$WEB/package.json" ]; then
    warn "no console source in $SRC — skipped"
elif ! command -v npm >/dev/null 2>&1; then
    warn "npm unavailable — console frontend NOT rebuilt"
else
    if _quiet "installing frontend dependencies" _retry 2 _npm_deps && _quiet "building the console" _build; then
        BUILD_OK=1
        [ -f "$WEB/.tsc-failed" ] && warn "type check reported errors — built anyway (see $LOG)"
        rm -f "$WEB/.tsc-failed"
    else
        warn "frontend build failed — the previous build stays live (see $LOG)"
        rm -rf "$WEB/dist.next"
    fi
fi
if [ "$BUILD_OK" = 1 ]; then
    rm -rf "$WEB/dist.prev"
    [ -d "$WEB/dist" ] && mv "$WEB/dist" "$WEB/dist.prev"
    if mv "$WEB/dist.next" "$WEB/dist"; then ok "new build in place ($(grep -o 'assets/index-[^"]*\.js' "$WEB/dist/index.html" | head -1))"
    else [ -d "$WEB/dist.prev" ] && mv "$WEB/dist.prev" "$WEB/dist"; BUILD_OK=0; warn "swap failed — previous build restored"; fi
fi

# ── 5. Services: refresh, self-heal, restart ────────────────────────────────
step "[5/6] Services"
OS="$(uname -s 2>/dev/null)"
SYSTEMD=0
if [ "$OS" = Linux ] && command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then SYSTEMD=1; fi

# Voice: the offline model for the configured language is re-checked (and
# re-fetched if an earlier download was interrupted) — never blocks the update.
if [ -x "$TOOL_PY" ]; then
    _vlang="$("$TOOL_PY" -c 'import json,os,pathlib
d=pathlib.Path(os.environ.get("VOICE_CONFIG_DIR") or pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home()/".config")/"corvin-voice")
try: p=json.loads((d/"profile.json").read_text()); print(p.get("display_language") or (p.get("identity") or {}).get("display_language") or "")
except Exception: print("")' 2>/dev/null)"
    [ "$_vlang" = en ] && _vlang=""
    _quiet "offline voice model (${_vlang:+$_vlang + }en)" "$TOOL_PY" -m corvinOS.shared.voice_models ${_vlang:-} en \
        || warn "offline voice model not ready — the console retries it; online voices still work"
fi

if [ "$SYSTEMD" = 1 ]; then
    if [ -x "$TOOL_PY" ] && { ! systemctl --user cat corvin-webui.service >/dev/null 2>&1 \
            || { [ -n "$(served_tree)" ] && [ "$(served_tree)" != "$SRC" ]; }; }; then
        # Units gone (deleted, profile reset), or pointing at a different
        # source tree than the one just built: re-provision them from $SRC.
        _quiet "re-registering services" corvin-install --yes || warn "service registration incomplete — run: corvin-install --yes"
    fi
    [ -f "$SRC/scripts/setup.sh" ] && { bash "$SRC/scripts/setup.sh" --no-autostart >>"$LOG" 2>&1 || true; }
fi

restart_all() {
    if [ "$SYSTEMD" = 1 ]; then
        systemctl --user daemon-reload >/dev/null 2>&1 || true
        for _u in $(systemctl --user list-unit-files 'corvin-*.service' --no-legend 2>/dev/null | awk '{print $1}'); do
            systemctl --user is-enabled --quiet "$_u" 2>/dev/null || continue
            systemctl --user restart "$_u" >>"$LOG" 2>&1 || true
        done
    elif [ "$OS" = Darwin ] && ls "$HOME"/Library/LaunchAgents/com.corvin.*.plist >/dev/null 2>&1; then
        for _p in "$HOME"/Library/LaunchAgents/com.corvin.*.plist; do
            launchctl kickstart -k "gui/$(id -u)/$(basename "$_p" .plist)" >>"$LOG" 2>&1 \
                || { launchctl unload "$_p" >/dev/null 2>&1; launchctl load "$_p" >>"$LOG" 2>&1; }
        done
    else
        pkill -f 'corvinos-serve|corvin_gateway.app' 2>/dev/null || true
        sleep 2
        nohup corvinos-serve --no-browser >>"$LOG" 2>&1 &
    fi
}

served_entry() {
    curl -s --noproxy '*' -m 5 "$BASE_URL/console/" 2>/dev/null | grep -o 'assets/index-[^"]*\.js' | head -1
}
wait_live() {  # wait_live SECONDS — /console/ must serve dist's entry bundle
    _want="$(grep -o 'assets/index-[^"]*\.js' "$WEB/dist/index.html" 2>/dev/null | head -1)"
    _i=0
    while [ "$_i" -lt "$1" ]; do
        _got="$(served_entry)"
        if [ -n "$_got" ] && { [ -z "$_want" ] || [ "$_got" = "$_want" ]; }; then return 0; fi
        sleep 2; _i=$((_i + 2))
    done
    echo "  served '${_got:-nothing}', expected '${_want:-any}'" >>"$LOG"
    return 1
}

if [ "$NO_RESTART" = 1 ]; then
    ok "restart skipped (--no-restart) — restart the console to load the update"
    exit 0
fi
restart_all
printf '  … waiting for the console '
if wait_live 180; then printf '%s\n' "$(_g '✓')"; LIVE=1; else printf '%s\n' "$(_r '✗')"; LIVE=0; fi

# ── 6. Verify (over the wire, like a browser) ───────────────────────────────
step "[6/6] Verify"
VERIFY_OK=0
if [ "$LIVE" = 1 ] && [ "$PKG_OK" = 1 ]; then
    _vpy="$TOOL_PY"; [ -x "$_vpy" ] || _vpy="$(command -v python3 || true)"
    if [ -n "$_vpy" ] && [ -f "$SRC/scripts/verify_install.py" ]; then
        "$_vpy" "$SRC/scripts/verify_install.py" --url "$BASE_URL" --dist "$WEB/dist" --wait 60 && VERIFY_OK=1
    else
        curl -fs --noproxy '*' -m 5 "$BASE_URL/v1/console/healthz" >/dev/null && VERIFY_OK=1
    fi
fi

if [ "$VERIFY_OK" = 1 ]; then
    printf '\n%s\n' "$(_g "$(_b 'CorvinOS is up to date and running.')")"
    printf '  %s/console/   (reload the tab: Ctrl+Shift+R / Cmd+Shift+R)\n' "$BASE_URL"
    [ -n "$BACKUP_REF" ] && printf '  previous code kept on branch: %s\n' "$BACKUP_REF"
    [ "$STASHED" = 1 ] && printf '  your local changes: git stash list  (re-apply: git stash pop)\n'
    exit 0
fi

# ── Rollback ────────────────────────────────────────────────────────────────
printf '\n  %s\n' "$(_r 'The updated console did not verify.')"
if [ "$ROLLBACK" != 1 ] || [ "$REBUILD_ONLY" = 1 ]; then
    echo "  Kept as is (--no-rollback / --rebuild-only). Log: $LOG"; exit 2
fi
step "Rolling back"
RB=1
if [ -n "$PREV_REV" ] && [ -d "$SRC/.git" ]; then
    _git reset -q --hard "$PREV_REV" >>"$LOG" 2>&1 || RB=0
elif [ -d "$SRC.prev" ]; then
    for _k in .corvin core/console/corvin_console/web-next/node_modules; do
        [ -e "$SRC/$_k" ] && rm -rf "$SRC.prev/$_k" && mv "$SRC/$_k" "$SRC.prev/$_k"
    done
    rm -rf "$SRC.failed"; mv "$SRC" "$SRC.failed" && mv "$SRC.prev" "$SRC" || RB=0
else
    # Fresh tree (e.g. PyPI → managed conversion): there is no previous code
    # to go back to. Reinstalling would reinstall the NEW code and report it
    # as "rolled back" — say so instead.
    echo "  no previous source revision to restore (first managed install)" >>"$LOG"
    RB=0
fi
if [ -d "$WEB/dist.prev" ]; then rm -rf "$WEB/dist"; mv "$WEB/dist.prev" "$WEB/dist" || RB=0; fi
_quiet "reinstalling the previous version" _uv_install_healing || RB=0
restart_all
if [ "$RB" = 1 ] && wait_live 180; then
    echo "  $(_y 'Rolled back — the previous version is running.') Log: $LOG"
    exit 1
fi
echo "  $(_r 'Rollback failed too.') Repair: sh install.sh   ·   Log: $LOG" >&2
exit 2
