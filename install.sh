#!/bin/sh
# install.sh — CorvinOS installer for Linux and macOS.
# Usage:
#   curl -fsSL https://corvin-labs.com/install.sh | sh
#   sh install.sh --editable /path/to/CorvinOS    # dev install from a local clone
#
# POSIX sh, ZERO prerequisites: it bootstraps `uv` (a single static binary that
# also manages its own Python), so you need NO system Python, NO pip, and NO
# package manager pre-installed. Idempotent — safe to re-run.
set -eu

PKG="${CORVIN_PKG:-corvinos}"
EDITABLE=""
SKIP_HERMES="${CORVIN_SKIP_HERMES:-0}"

_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }
die() { printf '%s %s\n' "$(_red 'Error:')" "$*" >&2; exit 1; }

# ── argument parsing ──────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        -e|--editable)
            [ $# -lt 2 ] && die "--editable requires a path argument"
            EDITABLE="$2"; shift 2 ;;
        --no-hermes)
            SKIP_HERMES=1; shift ;;
        *)
            die "Unknown argument: $1
Usage: $0 [--editable|-e <path>] [--no-hermes]" ;;
    esac
done
if [ -n "$EDITABLE" ]; then
    [ -d "$EDITABLE" ] || die "Editable path does not exist: $EDITABLE"
    EDITABLE="$(cd "$EDITABLE" && pwd)"
fi

printf '\n%s — self-hosted, local-first AI voice agent\n\n' "$(_bold 'CorvinOS installer')"

# ── 1. ensure uv (brings its own Python → zero prerequisites) ─────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "  Bootstrapping the uv runtime (brings its own Python) ..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        die "Need curl or wget to bootstrap uv. Please install one and re-run."
    fi
fi
# uv lands in ~/.local/bin (current) or ~/.cargo/bin (older installs)
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv >/dev/null 2>&1 || die "uv is not on PATH after install. Open a new terminal and re-run."
echo "  uv $(uv --version 2>/dev/null | awk '{print $2}') — $(_green OK)"

# ── 2. install CorvinOS as an isolated tool (uv fetches Python if needed) ─────
if [ -n "$EDITABLE" ]; then
    echo "  Installing CorvinOS (editable) from $EDITABLE ..."
    uv tool install --force --editable "$EDITABLE"
else
    # Query PyPI JSON API for the exact latest version so uv bypasses its
    # local resolver cache (which can lag behind a freshly published release).
    PINNED=""
    if command -v curl >/dev/null 2>&1; then
        PINNED=$(curl -fsSL --max-time 10 "https://pypi.org/pypi/${PKG}/json" 2>/dev/null \
                 | grep -o '"version":"[^"]*"' | head -1 | cut -d'"' -f4)
    fi
    if [ -n "$PINNED" ]; then
        echo "  Installing ${PKG}==${PINNED} ..."
        uv tool install --force "${PKG}==${PINNED}" || {
            # PyPI JSON reported the version but the simple index CDN may not
            # have propagated it yet — fall back to whatever is available.
            echo "  ⚠ ${PKG}==${PINNED} not yet on index — installing latest available ..."
            uv tool install --force --upgrade "$PKG"
        }
    else
        echo "  Installing $PKG (first run can take a minute) ..."
        uv tool install --force --upgrade "$PKG"
    fi
fi
uv tool update-shell >/dev/null 2>&1 || true   # persist ~/.local/bin on PATH

command -v corvinos-serve >/dev/null 2>&1 \
    || die "install succeeded but 'corvinos-serve' is not on PATH — open a new terminal and retry"

# ── 2b. Hermes (local offline engine): Ollama + model, working out of the box ──
# So CorvinOS runs fully offline with `--engine hermes` from the first start.
# Opt out with --no-hermes or CORVIN_SKIP_HERMES=1 (e.g. cloud-only / CI).
if [ "$SKIP_HERMES" != "1" ]; then
    echo ""
    echo "  Setting up Hermes (local offline engine) ..."
    OS="$(uname -s 2>/dev/null || echo unknown)"
    # pick a model by available RAM (small box → lighter model)
    ram_mb=8000
    if [ -r /proc/meminfo ]; then
        ram_mb=$(awk '/MemTotal/{printf "%d",$2/1024}' /proc/meminfo 2>/dev/null || echo 8000)
    elif command -v sysctl >/dev/null 2>&1; then
        ram_mb=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d",$1/1024/1024}' || echo 8000)
    fi
    if [ "$ram_mb" -lt 6000 ]; then HMODEL="qwen3:1.7b"; else HMODEL="qwen3:8b"; fi
    echo "  RAM ~${ram_mb} MB → model $HMODEL"

    # ensure Ollama is installed
    if ! command -v ollama >/dev/null 2>&1 && [ ! -x /usr/local/bin/ollama ]; then
        echo "  Installing Ollama ..."
        case "$OS" in
            Linux)  curl -fsSL https://ollama.com/install.sh | sh || printf '  %s Ollama install failed\n' "$(_yellow '⚠')" ;;
            Darwin) if command -v brew >/dev/null 2>&1; then brew install ollama
                    else printf '  %s Install Ollama from https://ollama.com/download\n' "$(_yellow '⚠')"; fi ;;
            *)      printf '  %s Install Ollama from https://ollama.com/download\n' "$(_yellow '⚠')" ;;
        esac
    fi
    export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin"

    # ensure the Ollama server is reachable (start it detached if needed)
    if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
        command -v ollama >/dev/null 2>&1 && nohup ollama serve >/dev/null 2>&1 &
        i=0; while [ "$i" -lt 30 ]; do
            sleep 1
            curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1 && break
            i=$((i + 1))
        done
    fi

    # pull the model so Hermes is immediately usable offline
    if command -v ollama >/dev/null 2>&1 && curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
        if curl -s http://localhost:11434/api/tags 2>/dev/null | grep -q "$HMODEL"; then
            printf '  %s Hermes model %s already present\n' "$(_green '✓')" "$HMODEL"
        else
            echo "  Pulling $HMODEL (one-time, a few GB) ..."
            if ollama pull "$HMODEL"; then
                printf '  %s Hermes ready — %s installed\n' "$(_green '✓')" "$HMODEL"
            else
                printf '  %s model pull failed — finish later with: ollama pull %s\n' "$(_yellow '⚠')" "$HMODEL"
            fi
        fi
    else
        printf '  %s Ollama not reachable — Hermes self-heals on first run (or see https://ollama.com/download)\n' "$(_yellow '⚠')"
    fi
fi

# ── 3. setup wizard (only on an interactive terminal, not when piped) ─────────
if command -v corvin-install >/dev/null 2>&1; then
    if [ -t 0 ]; then
        echo "  Launching setup wizard ..."; echo ""
        corvin-install || true
    else
        printf '\n  %s Piped install detected — run the wizard once your terminal is ready:\n\n    %s\n' \
            "$(_yellow 'Note:')" "$(_bold 'corvin-install')"
    fi
fi

# ── 4. start server + wait for readiness + auto-launch console ──────────────────
echo ""
echo "  Starting CorvinOS console server ..."

CONSOLE_URL="http://localhost:8765/console/"
MAX_RETRIES=30
RETRY_COUNT=0

# The setup wizard (corvin-install, step "start console") may already have
# started and health-waited the console. Only launch a fresh server if nothing
# is answering on 8765 — a second `corvinos-serve` would collide on the port,
# fail to bind silently, and leave a dead SERVER_PID in the cheat sheet.
if curl -s -m 2 http://localhost:8765/api/health >/dev/null 2>&1; then
    printf '  %s Console already running (started by the setup wizard).\n' "$(_green '✓')"
    SERVER_PID="$(pgrep -f corvinos-serve 2>/dev/null | head -1 || true)"
else
    nohup corvinos-serve >/dev/null 2>&1 &
    SERVER_PID=$!
fi

# Wait for server to be ready
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s -m 2 http://localhost:8765/api/health >/dev/null 2>&1; then
        printf '  %s Server is ready!\n' "$(_green '✓')"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    sleep 1
done

# Launch browser if server is ready
if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
    if [ -t 1 ]; then
        echo "  Launching CorvinOS console in your browser ..."
        if command -v open >/dev/null 2>&1; then
            open "$CONSOLE_URL" 2>/dev/null || true
        elif command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$CONSOLE_URL" 2>/dev/null || true
        elif command -v wslview >/dev/null 2>&1; then
            wslview "$CONSOLE_URL" 2>/dev/null || true
        fi
    fi
else
    printf '  %s Server startup timeout. Open manually: %s\n' "$(_yellow '⚠')" "$CONSOLE_URL"
fi

# ── done / cheat sheet ────────────────────────────────────────────────────────
cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 $(_green "$(_bold 'CorvinOS is ready!')")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 $(_bold 'Your console is running:')

     $(_dim '→ http://localhost:8765/console/')
     $(_dim '→ Background PID: '"$SERVER_PID")

 $(_dim 'To stop the server:')

     $(_bold 'kill '"$SERVER_PID"' || killall corvinos-serve')

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 $(_bold 'Commands')
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

   $(_bold 'corvinos-serve')      Start the web console
   $(_bold 'corvin-install')      Setup wizard (bridges, tokens, voice)
   $(_bold 'corvin-uninstall')    Remove CorvinOS
   $(_bold 'corvin-a2a')          Agent-to-agent pairing and messaging

 $(_dim 'Hermes (offline engine) was installed automatically.')  $(_dim 'Skip next time with --no-hermes.')

EOF
