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
# ADR-0184: Stufe 1 (start-at-login) already runs by default on an
# interactive terminal via corvin-install below; --autostart forces that
# same step even when piped (curl | sh has no TTY, see step 3). --always-on
# additionally opts into Stufe 2 (survives a reboot with NO login at all) —
# a real security-posture change (needs sudo), so it is never implied by
# --autostart and never the default.
FORCE_AUTOSTART=0
ALWAYS_ON=0

_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }
die() { printf '%s %s\n' "$(_red 'Error:')" "$*" >&2; exit 1; }

# Progress heartbeat for a long, otherwise-SILENT command: prints a dot every
# second while it runs so the user always sees "still working …", then ✓ / ⚠.
# `set -e`-safe: the `if wait` swallows a non-zero exit; the caller decides what
# a failure means. Use ONLY for silent steps — a command with its own progress
# (ollama pull, uv sync) should run plainly so its native bar shows through.
_await() {
    _aw_msg="$1"; shift
    printf '  %s %s ' "$(_dim '⏳')" "$_aw_msg"
    "$@" >/dev/null 2>&1 &
    _aw_pid=$!
    while kill -0 "$_aw_pid" 2>/dev/null; do printf '.'; sleep 1; done
    if wait "$_aw_pid"; then printf ' %s\n' "$(_green '✓')"; return 0
    else printf ' %s\n' "$(_yellow '⚠')"; return 1; fi
}

# ── argument parsing ──────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        -e|--editable)
            [ $# -lt 2 ] && die "--editable requires a path argument"
            EDITABLE="$2"; shift 2 ;;
        --no-hermes)
            SKIP_HERMES=1; shift ;;
        --autostart)
            FORCE_AUTOSTART=1; shift ;;
        --always-on)
            FORCE_AUTOSTART=1; ALWAYS_ON=1; shift ;;
        *)
            die "Unknown argument: $1
Usage: $0 [--editable|-e <path>] [--no-hermes] [--autostart] [--always-on]" ;;
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
    # INST-1: install UNPINNED. A `uv tool install corvinos==<ver>` writes that
    # exact pin into the uv receipt, after which `uv tool upgrade corvinos`
    # (the auto-update path in serve_backend.py / the install.ps1 supervisor)
    # respects the pin forever and exits 0 with "Nothing to upgrade" — silently
    # freezing auto-update. Installing unpinned keeps the receipt upgradeable.
    # The PyPI JSON query below is now used ONLY for a friendly log line.
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
    # --refresh bypasses uv's local index cache so a freshly published release
    # is picked up immediately, without pinning the version into the receipt.
    uv tool install --force --refresh "$PKG"
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
    # Guard against an empty / non-numeric probe result (e.g. sysctl failed and
    # awk emitted nothing): `[ "" -lt 6000 ]` would print a shell error. Default
    # to a conservative 8000 so we fall through to the standard model, and the
    # running engine still auto-selects whatever tag actually gets installed.
    case "$ram_mb" in ''|*[!0-9]*) ram_mb=8000 ;; esac
    # Three-tier ladder so the pulled model actually RUNS alongside the OS +
    # console. qwen3:8b (~5.2 GB weights) OOMs/swaps on a 6–8 GB box, so it is
    # reserved for ≥12 GB; 6–12 GB gets qwen3:4b (~2.6 GB); < 6 GB gets the
    # 1.7b. The running Hermes engine auto-selects whatever tag is actually
    # present (_pick_installed_qwen3), so a later manual pull upgrades it.
    if [ "$ram_mb" -lt 6000 ]; then HMODEL="qwen3:1.7b"
    elif [ "$ram_mb" -lt 12000 ]; then HMODEL="qwen3:4b"
    else HMODEL="qwen3:8b"; fi
    echo "  RAM ~${ram_mb} MB → model $HMODEL"

    # ensure Ollama is installed
    if ! command -v ollama >/dev/null 2>&1 && [ ! -x /usr/local/bin/ollama ]; then
        case "$OS" in
            # `curl -fsSL ... | sh` is a SILENT download (-s suppresses curl's own
            # meter) of the Ollama binary + runtime (~100+ MB) — with no heartbeat
            # this looked exactly like a hung installer on a slower connection.
            Linux)  _await "Downloading Ollama (~100 MB, one-time)" \
                        sh -c 'curl -fsSL https://ollama.com/install.sh | sh' \
                        || printf '  %s Ollama install failed — install manually: https://ollama.com/download\n' "$(_yellow '⚠')" ;;
            Darwin) echo "  Installing Ollama ..."
                    if command -v brew >/dev/null 2>&1; then brew install ollama
                    else printf '  %s Install Ollama from https://ollama.com/download\n' "$(_yellow '⚠')"; fi ;;
            *)      printf '  %s Install Ollama from https://ollama.com/download\n' "$(_yellow '⚠')" ;;
        esac
    fi
    export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin"

    # ensure the Ollama server is reachable (start it detached if needed)
    if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
        printf '  %s Starting Ollama service ' "$(_dim '⏳')"
        command -v ollama >/dev/null 2>&1 && nohup ollama serve >/dev/null 2>&1 &
        _ollama_ok=0; i=0; while [ "$i" -lt 30 ]; do
            sleep 1; printf '.'
            if curl -s -m 2 http://localhost:11434/api/tags >/dev/null 2>&1; then _ollama_ok=1; break; fi
            i=$((i + 1))
        done
        if [ "$_ollama_ok" = 1 ]; then printf ' %s\n' "$(_green 'ready')"
        else printf ' %s\n' "$(_yellow 'not ready yet')"; fi
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

    # ── Pre-warm the L44 safety classifier ───────────────────────────────────
    # The acceptable-use gate classifies EVERY message using the SAME model the
    # Hermes engine runs ($HMODEL). On a fresh box that model's first load was a
    # ~22 s COLD start (and it may still be finishing its download), so the first
    # message fell back to the deterministic Tier-0 floor instead of a real
    # semantic check. Pre-warm it now (one throwaway generation, keep_alive 30m so
    # it stays resident) → the very first real safety-check is instant and warm.
    # (We deliberately do NOT pin a tiny model here: qwen3:1.7b is fast but fails
    # the classifier JSON schema ~always, so it would be worse than the warm chat
    # model; the gate's Tier-0 floor still covers any low-quality verdict.)
    if command -v ollama >/dev/null 2>&1 \
       && curl -s http://localhost:11434/api/tags 2>/dev/null | grep -q "$HMODEL"; then
        _await "Warming up the safety classifier ($HMODEL)" \
            curl -s -m 180 http://localhost:11434/api/generate \
                -d "{\"model\":\"$HMODEL\",\"prompt\":\"ok\",\"stream\":false,\"keep_alive\":\"30m\"}" \
            || true
    fi
fi

# ── 3. setup wizard (voice provisioning + Stufe-1 login autostart) ────────────
# A fresh install must be voice-ready (Whisper STT + Piper TTS models) with zero
# manual steps — that is the product's core promise. The wizard provisions those
# models (installer steps ensure_stt / ensure_piper) plus registers the Stufe-1
# login services. On an interactive terminal it runs interactively; on a PIPED
# install (curl | sh, no TTY) it previously SKIPPED entirely, silently leaving
# STT/TTS unprovisioned and the console un-serviced while still printing
# "CorvinOS is ready!" — a broken voice-first first run. We now run it
# non-interactively (`--yes`) in the piped case so voice works out of the box.
# Stufe-2 always-on (survives reboot with no login, needs sudo) stays strictly
# opt-in via --always-on below — it is NEVER implied here.
if command -v corvin-install >/dev/null 2>&1; then
    if [ -t 0 ] && [ "$FORCE_AUTOSTART" != "1" ]; then
        echo "  Launching setup wizard ..."; echo ""
        corvin-install || true
    else
        # Piped (or --autostart): provision voice + services non-interactively so
        # the fresh install is genuinely voice-ready. Fail-soft — a failed model
        # download (e.g. offline) must not abort the whole install.
        echo "  Provisioning voice (STT + TTS) and services non-interactively ..."; echo ""
        corvin-install --yes || printf '  %s Voice/setup provisioning did not fully complete — re-run later with: %s\n' \
            "$(_yellow '⚠')" "$(_bold 'corvin-install')"
    fi
fi

# ── 3b. always-on (ADR-0184 Stufe 2, opt-in, needs sudo) ─────────────────────
# Deliberately separate from Stufe 1 above: this registers a system-level
# service that survives a reboot even if nobody ever logs in. Never runs
# silently — only when the user explicitly passed --always-on.
if [ "$ALWAYS_ON" = "1" ]; then
    echo ""
    echo "  Setting up always-on mode (survives reboot with no login) ..."
    # INST-5: corvin-service lives in ~/.local/bin, which is NOT on root's
    # sudo secure_path — a bare `sudo corvin-service` fails "command not
    # found". Resolve the absolute path in the user's PATH and hand THAT to
    # sudo (which also preserves SUDO_USER so current_user() won't pick root).
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

# ── 4. start server + wait for readiness + auto-launch console ──────────────────
echo ""
echo "  Starting CorvinOS console server ..."

CONSOLE_URL="http://localhost:8765/console/"
# Generous headroom so a slow cold start still gets a "ready" before we stop
# waiting; we open the browser regardless (see below) so the top-level goal
# "the console opens in the browser" holds even on a slow machine.
MAX_RETRIES=60
RETRY_COUNT=0
SERVER_READY=0

# The setup wizard (corvin-install, step "start console") may already have
# started and health-waited the console. Only launch a fresh server if nothing
# is answering on 8765 — a second `corvinos-serve` would collide on the port,
# fail to bind silently, and leave a dead SERVER_PID in the cheat sheet.
if curl -fs -m 2 http://localhost:8765/v1/console/healthz >/dev/null 2>&1; then
    printf '  %s Console already running (started by the setup wizard).\n' "$(_green '✓')"
    SERVER_PID="$(pgrep -f corvinos-serve 2>/dev/null | head -1 || true)"
else
    nohup corvinos-serve >/dev/null 2>&1 &
    SERVER_PID=$!
fi

# Wait for server to be ready. Live "still working" feedback -- a cold
# Python import + Gatekeeper/AV scanning a freshly spawned process can push
# this well past a few seconds with zero output otherwise, which reads as a
# hang. Same dot-per-second convention as _await above.
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

# Open the console no matter what. If the probe timed out the server is still
# coming up in the background, so the tab will connect on reload a few seconds
# later — the goal is that the console always opens, not that it opens instantly.
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
