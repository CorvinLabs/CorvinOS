#!/bin/bash
# install.sh — CorvinOS installer for Linux and macOS.
# Includes auto-detection and installation of Claude Code.
#
# Installs ONLY from a local CorvinOS checkout — there is no download mode.
#
# Usage:
#   git clone https://github.com/CorvinLabs/CorvinOS.git && cd CorvinOS
#   ./install.sh                                  # install from this checkout
#   sh install.sh --editable /path/to/CorvinOS    # install from another checkout
#   sh install.sh --no-claude-code                # skip Claude Code installation
#
# POSIX sh, ZERO prerequisites: it bootstraps `uv` (a single static binary that
# also manages its own Python), so you need NO system Python, NO pip, and NO
# package manager pre-installed. Self-contained + Production Ready.
#
# Supply-chain pins (2026-09-10 adversarial review, ADR-0666):
#   * uv    — PINNED. The installer script for exactly UV_PIN_VERSION is fetched
#             from the immutable GitHub release asset, its SHA-256 is verified
#             against UV_INSTALLER_SHA256 below BEFORE it runs, and that script
#             in turn verifies the uv binary it downloads against its own
#             embedded checksums. No `curl | sh` of a moving target.
#   * corvinos — installed EDITABLE from the local checkout; never fetched
#             from an index.
#   * claude — OPTIONAL. Auto-installs via official installer if available.
set -eu

PKG="${CORVIN_PKG:-corvinos}"
UV_PIN_VERSION="0.12.9"
UV_INSTALLER_URL="https://github.com/astral-sh/uv/releases/download/${UV_PIN_VERSION}/uv-installer.sh"
UV_INSTALLER_SHA256="222e006c0fe4a0d793031833e469b21df72311f4e3526ffecca0e19e6dfabc32"

INSTALL_LOG="${CORVIN_INSTALL_LOG:-${TMPDIR:-/tmp}/corvinos-install.log}"
OPEN_LAN=0
EDITABLE=""
SKIP_CLAUDE=0
FORCE_AUTOSTART=0
ALWAYS_ON=0
PRESET=""
CORVIN_REPO_URL="https://github.com/CorvinLabs/CorvinOS"

# ─────────────────────────────────────────────────────────────────────────────
# Styling utilities
# ─────────────────────────────────────────────────────────────────────────────
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }
die() { printf '%s %s\n' "$(_red 'Error:')" "$*" >&2; exit 1; }

_await() {
    _aw_msg="$1"; shift
    printf '  %s %s ' "$(_dim '⏳')" "$_aw_msg"
    printf '\n=== %s: %s ===\n' "$(date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo now)" "$_aw_msg" >>"$INSTALL_LOG" 2>/dev/null || true
    "$@" </dev/null >>"$INSTALL_LOG" 2>&1 &
    _aw_pid=$!
    while kill -0 "$_aw_pid" 2>/dev/null; do printf '.'; sleep 1; done
    if wait "$_aw_pid"; then printf ' %s\n' "$(_green '✓')"; return 0
    else printf ' %s  %s\n' "$(_yellow '⚠')" "$(_dim "(details: $INSTALL_LOG)")"; return 1; fi
}

_sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | awk '{print $NF}'
    else echo ""; fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        -e|--editable)
            [ $# -lt 2 ] && die "--editable requires a path argument"
            EDITABLE="$2"; shift 2 ;;
        --autostart)
            FORCE_AUTOSTART=1; shift ;;
        --always-on)
            FORCE_AUTOSTART=1; ALWAYS_ON=1; shift ;;
        --preset)
            [ $# -lt 2 ] && die "--preset requires an argument (minimal|standard|advanced)"
            PRESET="$2"; shift 2 ;;
        --lan)
            OPEN_LAN=1; shift ;;
        --no-claude-code)
            SKIP_CLAUDE=1; shift ;;
        *)
            die "Unknown argument: $1
Usage: $0 [--editable|-e <path>] [--autostart] [--always-on] [--lan] [--preset {minimal|standard|advanced}] [--no-claude-code]" ;;
    esac
done

if [ -n "$EDITABLE" ]; then
    [ -d "$EDITABLE" ] || die "Editable path does not exist: $EDITABLE"
    EDITABLE="$(cd "$EDITABLE" && pwd)"
fi

# The source is always a local checkout: --editable PATH, else the checkout
# this script sits in. Decided before anything is downloaded, so a run outside
# a checkout fails in the first second instead of after the uv/Node bootstrap.
_is_checkout() { [ -f "$1/.corvin_repo" ] && [ -f "$1/pyproject.toml" ]; }
_not_a_checkout() {
    die "$1
CorvinOS installs only from a local clone of the repository:
  git clone ${CORVIN_REPO_URL}.git
  cd CorvinOS
  ./install.sh"
}
if [ -n "$EDITABLE" ]; then
    _is_checkout "$EDITABLE" || _not_a_checkout "Not a CorvinOS checkout (missing .corvin_repo or pyproject.toml): $EDITABLE"
else
    _script_dir=""
    # Parameter expansion, not dirname: this runs before PATH is set up.
    case "$0" in
        */install.sh) _script_dir="$(cd "${0%/*}" 2>/dev/null && pwd || true)" ;;
        install.sh)   _script_dir="$(pwd)" ;;
    esac
    if [ -n "$_script_dir" ] && _is_checkout "$_script_dir"; then
        EDITABLE="$_script_dir"
    else
        _not_a_checkout "install.sh must be run from inside a CorvinOS checkout (or pass --editable <checkout>)."
    fi
fi

if [ -n "$PRESET" ]; then
    case "$PRESET" in
        minimal|standard|advanced) ;;
        *) die "Invalid preset: $PRESET. Must be one of: minimal, standard, advanced" ;;
    esac
fi

printf '\n%s — self-hosted, local-first AI operating system\n\n' "$(_bold 'CorvinOS installer')"

# One installer/updater at a time: two concurrent `uv tool install --force`
# runs corrupt the tool venv, two SPA builds race on dist/. mkdir is atomic on
# every filesystem (flock is not on macOS). The lock records its owner's PID:
# a lock whose owner is gone (killed run, reboot) is stale and taken over —
# otherwise it would block every later install AND keep the watchdog, which
# stands down while the lock is held, silent forever. Older than 2 h: stale.
LOCK_DIR="${TMPDIR:-/tmp}/corvinos-setup.lock"
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
# No coreutils on PATH at all → no lock (the checks below then report what
# is actually missing, instead of a phantom "another install is running").
if command -v mkdir >/dev/null 2>&1 && command -v rm >/dev/null 2>&1; then
    _take_lock || die "another CorvinOS install/update is running (lock: $LOCK_DIR, pid $(cat "$LOCK_DIR/pid" 2>/dev/null))."
    trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM
fi

# Corporate networks (Citrix, Zscaler, TLS-inspecting proxies) re-sign HTTPS
# with a company CA that only the OS trust store knows. uv and git honour the
# system store with these; harmless where no proxy exists.
export UV_NATIVE_TLS="${UV_NATIVE_TLS:-1}"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 0: Ensure curl/wget for downloads
# ─────────────────────────────────────────────────────────────────────────────
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    echo "  Installing curl (required for downloads) ..."
    if command -v apt-get >/dev/null 2>&1; then
        if [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then
            apt-get update >/dev/null 2>&1 && apt-get install -y curl >/dev/null 2>&1
        else
            command -v sudo >/dev/null 2>&1 && sudo apt-get update >/dev/null 2>&1 && sudo apt-get install -y curl >/dev/null 2>&1
        fi
    elif command -v brew >/dev/null 2>&1; then
        brew install curl >/dev/null 2>&1
    elif command -v yum >/dev/null 2>&1; then
        sudo yum install -y curl >/dev/null 2>&1
    else
        die "curl or wget not found, and no package manager detected. Please install curl manually."
    fi
fi
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
    || die "curl or wget still not available after install attempt"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Bootstrap uv (brings its own Python)
# ─────────────────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "  Bootstrapping the uv ${UV_PIN_VERSION} runtime (brings its own Python) ..."
    _uv_tmp="$(mktemp "${TMPDIR:-/tmp}/uv-installer.XXXXXX")" || die "mktemp failed"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 300 -o "$_uv_tmp" "$UV_INSTALLER_URL" \
            || { rm -f "$_uv_tmp"; die "could not download $UV_INSTALLER_URL"; }
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$_uv_tmp" "$UV_INSTALLER_URL" \
            || { rm -f "$_uv_tmp"; die "could not download $UV_INSTALLER_URL"; }
    else
        rm -f "$_uv_tmp"; die "Need curl or wget to bootstrap uv. Please install one and re-run."
    fi
    _uv_sum="$(_sha256_of "$_uv_tmp")"
    [ -n "$_uv_sum" ] || { rm -f "$_uv_tmp"; die "no sha256sum/shasum/openssl available to verify the uv installer"; }
    if [ "$_uv_sum" != "$UV_INSTALLER_SHA256" ]; then
        rm -f "$_uv_tmp"
        die "uv installer checksum mismatch (expected $UV_INSTALLER_SHA256, got $_uv_sum) — refusing to run it"
    fi
    sh "$_uv_tmp"
    rm -f "$_uv_tmp"
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv >/dev/null 2>&1 || die "uv is not on PATH after install. Open a new terminal and re-run."
echo "  uv $(uv --version 2>/dev/null | awk '{print $2}') — $(_green OK)"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1b: Claude Code detection & installation (optional, Production Ready)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$SKIP_CLAUDE" != "1" ]; then
    CLAUDE_CODE_PATH=""

    # Try to find existing Claude Code installation
    if command -v claude >/dev/null 2>&1; then
        CLAUDE_CODE_PATH="$(command -v claude)"
        echo "  Claude Code found at $CLAUDE_CODE_PATH — $(_green OK)"
    else
        # Install it without asking — the console chat runs on it, and the
        # installer is unattended by design (one run, no questions).
        echo "  Claude Code not found — installing it ..."
        _claude_install_sh="$(mktemp "${TMPDIR:-/tmp}/claude-install.XXXXXX")" || die "mktemp failed"
        if curl -fsSL --max-time 60 -o "$_claude_install_sh" "https://claude.ai/install.sh" 2>/dev/null \
           && _await "Installing Claude Code" sh "$_claude_install_sh"; then
            export PATH="$HOME/.local/bin:$HOME/.claude/local:$PATH"
            CLAUDE_CODE_PATH="$(command -v claude 2>/dev/null || true)"
            [ -n "$CLAUDE_CODE_PATH" ] || printf '  %s Claude Code installed but not on PATH yet — open a new terminal later.\n' "$(_yellow '⚠')"
        else
            printf '  %s Claude Code install failed — continuing; chat needs it (https://claude.ai/install.sh).\n' "$(_yellow '⚠')"
        fi
        rm -f "$_claude_install_sh"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1a: Source tree — the local checkout resolved right after arg parsing
# ─────────────────────────────────────────────────────────────────────────────
_retry() {  # _retry N cmd… — exponential backoff 2,4,8 s
    _rt_n="$1"; shift; _rt_i=1; _rt_wait=2
    while :; do
        "$@" && return 0
        [ "$_rt_i" -ge "$_rt_n" ] && return 1
        printf '  %s attempt %s/%s failed — retrying in %ss\n' "$(_yellow '↻')" "$_rt_i" "$_rt_n" "$_rt_wait" >&2
        sleep "$_rt_wait"; _rt_i=$((_rt_i + 1)); _rt_wait=$((_rt_wait * 2))
    done
}

echo "  Source: local checkout ($EDITABLE)"
REPO_DIR="$EDITABLE"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1b: Bootstrap local Node.js runtime (self-contained, no sudo)
# ─────────────────────────────────────────────────────────────────────────────
if [ -f "${REPO_DIR}/scripts/ensure-node.sh" ]; then
    echo "  Bootstrapping local Node.js runtime ..."
    if ! _retry 2 bash "${REPO_DIR}/scripts/ensure-node.sh"; then
        echo "  Node.js bootstrap failed — falling back to system Node.js"
    fi
fi
# ensure-node.sh runs in a child process, so its PATH export dies with it.
# Export the local runtime here, or corvin-install's console step reports
# "npm not found", skips the SPA build and the console serves a 503 page.
_corvin_node_bin="${CORVIN_HOME:-$HOME/.corvin}/node/bin"
if [ -x "${_corvin_node_bin}/npm" ]; then
    export PATH="${_corvin_node_bin}:$PATH"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1c: Cross-platform compatibility check (ADR-0666 supplement)
# ─────────────────────────────────────────────────────────────────────────────
REPAIR_SCRIPT="$REPO_DIR/scripts/install_repair.sh"
if [ -f "$REPAIR_SCRIPT" ]; then
    echo "  Checking cross-platform compatibility (operator→corvin_operator rename) ..."
    if ! bash "$REPAIR_SCRIPT" --diagnose >/dev/null 2>&1; then
        printf '  %s Platform issues detected. Attempting repair...\n' "$(_yellow '⚠')"
        if bash "$REPAIR_SCRIPT" --repair; then
            printf '  %s Platform issues fixed.\n' "$(_green '✓')"
        else
            die "Platform compatibility repair failed. Please run manually: bash $REPAIR_SCRIPT --repair --force"
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Install CorvinOS via uv
# ─────────────────────────────────────────────────────────────────────────────
# Self-healing: a half-written tool venv (killed install, AV lock, full disk)
# makes every later `uv tool install` fail on the broken receipt — the second
# attempt removes the venv first; the third also drops uv's cache.
_uv_install() {
    uv tool install --force --editable "${EDITABLE}[browser]"
}
_uv_install_healing() {
    _uv_install && return 0
    printf '  %s install failed — removing the tool environment and retrying\n' "$(_yellow '↻')"
    uv tool uninstall "$PKG" >/dev/null 2>&1 || rm -rf "$(uv tool dir 2>/dev/null)/$PKG"
    _uv_install && return 0
    printf '  %s retrying once more with a clean uv cache\n' "$(_yellow '↻')"
    uv cache clean "$PKG" >/dev/null 2>&1 || true
    _uv_install
}
_await "Installing CorvinOS from $EDITABLE" _uv_install_healing \
    || die "uv tool install failed — details: $INSTALL_LOG"
uv tool update-shell >/dev/null 2>&1 || true
export PATH="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"):$PATH"

command -v corvinos-serve >/dev/null 2>&1 \
    || die "install succeeded but 'corvinos-serve' is not on PATH — open a new terminal and retry"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Setup voice & services (STT/TTS provisioning)
# ─────────────────────────────────────────────────────────────────────────────
# Always unattended (--yes): every dependency decision is made automatically.
# Bridges, tokens and API keys are configured afterwards in the console
# (Settings → Bridges) or by re-running `corvin-install` as a wizard.
if command -v corvin-install >/dev/null 2>&1; then
    echo "  Provisioning dependencies, voice (STT + TTS) and services ..."; echo ""
    corvin-install --yes || printf '  %s Provisioning did not fully complete — re-run later with: %s\n' \
        "$(_yellow '⚠')" "$(_bold 'corvin-install --yes')"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3a: Preset setup (optional configuration)
# ─────────────────────────────────────────────────────────────────────────────
if [ -n "$PRESET" ]; then
    echo ""
    echo "  Configuring preset: $PRESET ..."
    if command -v corvin-preset-setup >/dev/null 2>&1; then
        corvin-preset-setup "$PRESET" --quiet || printf '  %s Could not set preset (will use default "standard")\n' "$(_yellow '⚠')"
    else
        printf '  %s corvin-preset-setup not found (preset not configured, will use default)\n' "$(_yellow '⚠')"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3d: Watchdog setup (systemd service installation) [ADR-0867]
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "  Setting up watchdog service (health monitoring) ..."
SETUP_SCRIPT="${REPO_DIR}/scripts/setup.sh"

if [ -f "$SETUP_SCRIPT" ]; then
    # Started now, not "at next login": it stands down while this script holds
    # the setup lock, then guards the console from the first minute on.
    if bash "$SETUP_SCRIPT"; then
        printf '  %s Watchdog service installed and running.\n' "$(_green '✓')"
    else
        printf '  %s Watchdog setup encountered an error — continuing anyway.\n' "$(_yellow '⚠')"
    fi
else
    printf '  %s Watchdog setup script not found at %s (skipping)\n' "$(_dim 'ℹ')" "$SETUP_SCRIPT"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3b: Always-on mode (optional, survives reboot)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$ALWAYS_ON" = "1" ]; then
    echo ""
    echo "  Setting up always-on mode (survives reboot with no login) ..."
    CORVIN_SERVICE_BIN="$(command -v corvin-service 2>/dev/null || true)"
    [ -n "$CORVIN_SERVICE_BIN" ] || CORVIN_SERVICE_BIN="corvin-service"
    if command -v sudo >/dev/null 2>&1; then
        if sudo "$CORVIN_SERVICE_BIN" install; then
            printf '  %s Always-on mode active.\n' "$(_green '✓')"
        else
            printf '  %s Could not enable always-on mode automatically.\n    Run manually: %s\n' \
                "$(_yellow '⚠')" "$(_bold "sudo $CORVIN_SERVICE_BIN install")"
        fi
    else
        printf '  %s sudo not found — run as root manually: %s\n' \
            "$(_yellow '⚠')" "$(_bold "$CORVIN_SERVICE_BIN install")"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3c: Firewall configuration (Linux ufw, optional)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$OPEN_LAN" = "1" ] && [ "$(uname -s 2>/dev/null || echo unknown)" = "Linux" ] && command -v ufw >/dev/null 2>&1; then
    if ufw status 2>/dev/null | grep -q "Status: active"; then
        if ufw allow 8765/tcp comment "CorvinOS console/A2A" >/dev/null 2>&1; then
            printf '  %s ufw: allowed inbound TCP 8765 for pairing with devices on this network.\n' "$(_green '✓')"
        else
            printf '  %s ufw is active but this account cannot add rules — if pairing with another device on your network shows the peer as "unreachable", run: %s\n' \
                "$(_yellow '⚠')" "$(_bold 'sudo ufw allow 8765/tcp')"
        fi
    fi
elif [ "$OPEN_LAN" != "1" ]; then
    printf '  %s Firewall untouched (console listens on 127.0.0.1). For A2A pairing over your LAN re-run with %s or open TCP 8765 yourself.\n' \
        "$(_dim 'ℹ')" "$(_bold '--lan')"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Start console server & launch browser
# ─────────────────────────────────────────────────────────────────────────────
# "Up" means the SPA answers 200 on /console/ — not merely /healthz. A console
# that booted while web-next/dist/ was empty keeps serving the 503 "build
# failed" fallback until it is restarted, even after the build lands, and its
# healthz stays green the whole time.
CONSOLE_URL="http://localhost:8765/console/"
HEALTHZ_URL="http://localhost:8765/v1/console/healthz"
SERVER_PID=""
USE_SYSTEMD=0
if [ "$(uname -s 2>/dev/null)" = "Linux" ] && command -v systemctl >/dev/null 2>&1 \
   && systemctl --user cat corvin-webui.service >/dev/null 2>&1; then
    USE_SYSTEMD=1
fi

_console_code() { curl -s -o /dev/null -m 3 -w '%{http_code}' "$CONSOLE_URL" 2>/dev/null || true; }

_wait_console() {
    _wc_i=0
    printf '  %s waiting for the console ' "$(_dim '⏳')"
    while [ "$_wc_i" -lt "$1" ]; do
        _wc_code="$(_console_code)"
        if [ "$_wc_code" = "200" ]; then printf ' %s (%ss)\n' "$(_green '✓')" "$_wc_i"; return 0; fi
        _wc_i=$((_wc_i + 1)); printf '.'; sleep 1
    done
    printf ' %s (last HTTP status: %s)\n' "$(_red '✗')" "${_wc_code:-none}"
    return 1
}

_start_console() {
    if [ "$USE_SYSTEMD" = "1" ]; then
        systemctl --user restart corvin-webui.service
    else
        pkill -f corvinos-serve 2>/dev/null || true
        # --no-browser: this script opens the tab itself below; without it the
        # operator gets two tabs.
        nohup corvinos-serve --no-browser >>"$INSTALL_LOG" 2>&1 &
        SERVER_PID=$!
    fi
}

# The SPA must be built before the console boots. corvin-install builds it;
# if that step was skipped (no npm, build error), build it here once.
_tool_py="$(uv tool dir 2>/dev/null)/${PKG}/bin/python"
SPA_DIR=""
if [ -x "$_tool_py" ]; then
    SPA_DIR="$("$_tool_py" -c 'import corvin_console, os; print(os.path.join(os.path.dirname(corvin_console.__file__), "web-next"))' 2>/dev/null || true)"
fi
if [ -n "$SPA_DIR" ] && [ -f "$SPA_DIR/package.json" ] && [ ! -f "$SPA_DIR/dist/index.html" ]; then
    if command -v npm >/dev/null 2>&1; then
        _await "Building the console frontend (one-time, ~1-2 min)" \
            sh -c "cd '$SPA_DIR' && rm -rf node_modules/.vite && npm install --no-audit --no-fund && npm run build" \
            || die "console frontend build failed — see $INSTALL_LOG"
    else
        die "the console frontend is not built and npm is unavailable. Install Node.js 20+ and re-run."
    fi
fi

echo ""
echo "  Starting CorvinOS console server ..."
if [ "$(_console_code)" = "200" ]; then
    printf '  %s Console already running.\n' "$(_green '✓')"
else
    _start_console
fi

if ! _wait_console 90; then
    # One restart covers the "booted before dist/ existed" case.
    printf '  %s Console not serving the UI yet — restarting it once ...\n' "$(_yellow '⚠')"
    _start_console
    if ! _wait_console 90; then
        printf '%s — the console did not come up at %s\n' "$(_red 'Error')" "$CONSOLE_URL"
        if [ "$USE_SYSTEMD" = "1" ]; then
            printf '  Logs: %s\n' "$(_bold 'journalctl --user -u corvin-webui -n 100')"
        else
            printf '  Logs: %s\n' "$(_bold "$INSTALL_LOG")"
        fi
        exit 2
    fi
fi
curl -fs -m 3 "$HEALTHZ_URL" >/dev/null 2>&1 \
    || printf '  %s %s did not answer — the UI loads, but check the console logs.\n' "$(_yellow '⚠')" "$HEALTHZ_URL"

if [ -t 1 ] || [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    echo "  Launching CorvinOS console in your browser ..."
    if command -v open >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ]; then
        open "$CONSOLE_URL" 2>/dev/null || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$CONSOLE_URL" >/dev/null 2>&1 || true
    elif command -v wslview >/dev/null 2>&1; then
        wslview "$CONSOLE_URL" 2>/dev/null || true
    fi
fi

if [ "$USE_SYSTEMD" = "1" ]; then
    RUN_INFO="systemd user service corvin-webui"
    STOP_CMD="systemctl --user stop corvin-webui"
else
    RUN_INFO="background PID ${SERVER_PID:-$(pgrep -f corvinos-serve 2>/dev/null | head -1)}"
    STOP_CMD="pkill -f corvinos-serve"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary & Quick Start Guide
# ─────────────────────────────────────────────────────────────────────────────
cat <<EOF

$(_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
 $(_green "$(_bold 'CorvinOS is ready!')")
$(_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

 $(_bold 'Your console is running:')

     $(_dim '→ http://localhost:8765/console/')
     $(_dim "→ $RUN_INFO")

 $(_dim 'To stop the server:')

     $(_bold "$STOP_CMD")

$(_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
 $(_bold 'Commands')
$(_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

   $(_bold 'corvinos-serve')      Start the web console
   $(_bold 'corvin-install')      Setup wizard (bridges, tokens, voice)
   $(_bold 'corvin-uninstall')    Remove CorvinOS
   $(_bold 'corvin-a2a')          Agent-to-agent pairing and messaging

 $(_dim 'Installation is fast (~30 MB with voice models).')

$(_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

EOF
