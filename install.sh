#!/bin/bash
# install.sh — CorvinOS installer for Linux and macOS.
# Includes auto-detection and installation of Claude Code.
#
# Usage:
#   curl -fsSL https://corvin-labs.com/install.sh | sh
#   sh install.sh --editable /path/to/CorvinOS    # dev install from a local clone
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
#   * corvinos — a version FLOOR (`corvinos>=CORVIN_MIN_VERSION`), not an exact
#             pin, on purpose (INST-1): `uv tool install corvinos==X` writes X
#             into the uv receipt and `uv tool upgrade corvinos` (the console's
#             auto-update path) then honours it forever — silently freezing
#             updates. The floor rejects a downgraded/stale index while keeping
#             the receipt upgradeable.
#   * claude — OPTIONAL. Auto-installs via official installer if available.
set -eu

PKG="${CORVIN_PKG:-corvinos}"
CORVIN_MIN_VERSION="2.0.0"
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

if [ -n "$PRESET" ]; then
    case "$PRESET" in
        minimal|standard|advanced) ;;
        *) die "Invalid preset: $PRESET. Must be one of: minimal, standard, advanced" ;;
    esac
fi

printf '\n%s — self-hosted, local-first AI operating system\n\n' "$(_bold 'CorvinOS installer')"

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
        # Offer to install Claude Code (optional)
        if [ -t 0 ]; then
            printf '  %s Claude Code not found. Install it now? (y/n) ' "$(_yellow '?')"
            read -r _install_claude
            if [ "$_install_claude" = "y" ] || [ "$_install_claude" = "Y" ]; then
                echo "  Installing Claude Code ..."
                if command -v curl >/dev/null 2>&1; then
                    _claude_install_sh="$(mktemp "${TMPDIR:-/tmp}/claude-install.XXXXXX")" || die "mktemp failed"
                    if curl -fsSL --max-time 60 -o "$_claude_install_sh" "https://claude.ai/install.sh" 2>/dev/null; then
                        chmod +x "$_claude_install_sh"
                        if sh "$_claude_install_sh"; then
                            CLAUDE_CODE_PATH="$(command -v claude 2>/dev/null || true)"
                            if [ -n "$CLAUDE_CODE_PATH" ]; then
                                echo "  Claude Code installed — $(_green OK)"
                            else
                                echo "  Claude Code installed but path not found — $(_yellow 'continuing anyway')"
                            fi
                        else
                            echo "  Claude Code install failed — $(_yellow 'continuing without it')"
                        fi
                    else
                        echo "  Could not download Claude Code installer — $(_yellow 'continuing without it')"
                    fi
                    rm -f "$_claude_install_sh"
                fi
            fi
        else
            # Non-interactive: skip Claude Code install offer
            echo "  Claude Code not found (non-interactive mode) — $(_dim 'skipped')"
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Install CorvinOS via uv
# ─────────────────────────────────────────────────────────────────────────────
if [ -n "$EDITABLE" ]; then
    echo "  Installing CorvinOS (editable) from $EDITABLE ..."
    uv tool install --force --editable "${EDITABLE}[browser]"
else
    LATEST=""
    if command -v curl >/dev/null 2>&1; then
        LATEST=$(curl -fsSL --max-time 10 "https://pypi.org/pypi/${PKG}/json" 2>/dev/null \
                 | grep -o '"version":"[^"]*"' | head -1 | cut -d'"' -f4)
    fi
    if [ -n "$LATEST" ]; then
        echo "  Installing ${PKG} (latest on PyPI: ${LATEST}) ..."
    else
        echo "  Installing $PKG (first run can take a minute) ..."
    fi
    if [ "$PKG" = "corvinos" ]; then
        uv tool install --force --refresh "${PKG}[browser]>=${CORVIN_MIN_VERSION}"
    else
        uv tool install --force --refresh "${PKG}[browser]"
    fi
fi
uv tool update-shell >/dev/null 2>&1 || true

command -v corvinos-serve >/dev/null 2>&1 \
    || die "install succeeded but 'corvinos-serve' is not on PATH — open a new terminal and retry"

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Setup voice & services (STT/TTS provisioning)
# ─────────────────────────────────────────────────────────────────────────────
if command -v corvin-install >/dev/null 2>&1; then
    if [ -t 0 ] && [ "$FORCE_AUTOSTART" != "1" ]; then
        echo "  Launching setup wizard ..."; echo ""
        corvin-install || true
    else
        echo "  Provisioning voice (STT + TTS) and services non-interactively ..."; echo ""
        corvin-install --yes || printf '  %s Voice/setup provisioning did not fully complete — re-run later with: %s\n' \
            "$(_yellow '⚠')" "$(_bold 'corvin-install')"
    fi
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
echo ""
echo "  Starting CorvinOS console server ..."

CONSOLE_URL="http://localhost:8765/console/"
MAX_RETRIES=60
RETRY_COUNT=0
SERVER_READY=0

if curl -fs -m 2 http://localhost:8765/v1/console/healthz >/dev/null 2>&1; then
    printf '  %s Console already running (started by the setup wizard).\n' "$(_green '✓')"
    SERVER_PID="$(pgrep -f corvinos-serve 2>/dev/null | head -1 || true)"
else
    nohup corvinos-serve >/dev/null 2>&1 &
    SERVER_PID=$!
fi

printf '  %s waiting for server to come up ' "$(_dim '⏳')"
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -fs -m 2 http://localhost:8765/v1/console/healthz >/dev/null 2>&1; then
        printf ' %s Server is ready! (%ss)\n' "$(_green '✓')" "$RETRY_COUNT"
        SERVER_READY=1
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    printf '.'
    sleep 1
done
[ "$SERVER_READY" -ne 1 ] && printf '\n'

if [ "$SERVER_READY" -ne 1 ]; then
    printf '  %s Server is taking longer than expected — opening the console anyway; reload the tab if it does not connect immediately: %s\n' "$(_yellow '⚠')" "$CONSOLE_URL"
fi

if [ -t 1 ]; then
    [ "$SERVER_READY" -eq 1 ] && echo "  Launching CorvinOS console in your browser ..."
    if command -v open >/dev/null 2>&1; then
        open "$CONSOLE_URL" 2>/dev/null || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$CONSOLE_URL" 2>/dev/null || true
    elif command -v wslview >/dev/null 2>&1; then
        wslview "$CONSOLE_URL" 2>/dev/null || true
    fi
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
     $(_dim '→ Background PID: '"$SERVER_PID")

 $(_dim 'To stop the server:')

     $(_bold 'kill '"$SERVER_PID"' || killall corvinos-serve')

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
