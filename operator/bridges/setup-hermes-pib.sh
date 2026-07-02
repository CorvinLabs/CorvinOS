#!/usr/bin/env bash
# setup-hermes-pib.sh — Public Installation Bundle for Hermes fallback engine.
#
# Ensures Hermes (Ollama + qwen3 model) is installed and ready as a reliable
# fallback for the Claude-Code bridge. Part of the ACO L5 self-healing system
# (ADR-0178). Run this once after initial setup or whenever Hermes is needed.
#
# Usage:
#   bash setup-hermes-pib.sh          — auto-detect platform and install
#   bash setup-hermes-pib.sh [--check] — verify installation without changes
#   bash setup-hermes-pib.sh [--repair] — repair a broken installation

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colour codes
GREEN='\033[1;32m'; RED='\033[1;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
fail() { printf "${RED}✗${NC} %s\n" "$*"; return 1; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
step() { printf "\n${GREEN}==${NC} %s\n" "$*"; }

# Mode detection
MODE="${1:-install}"
case "$MODE" in
  install|--install) MODE="install" ;;
  check|--check)     MODE="check" ;;
  repair|--repair)   MODE="repair" ;;
  *)                 fail "Unknown mode: $MODE (use: install, check, repair)"; exit 1 ;;
esac

# ── Import hermes_bootstrap helpers ───────────────────────────────────────────

# Inline key functions from hermes_bootstrap.py for portability
get_available_ram_gb() {
  if [[ -f /proc/meminfo ]]; then
    grep "MemTotal:" /proc/meminfo | awk '{print $2 / 1024 / 1024}' || echo 4.0
  elif command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.memsize 2>/dev/null | awk '{print $1 / 1024^3}' || echo 4.0
  else
    echo 4.0
  fi
}

select_model_for_ram() {
  local ram="$1"
  if (( $(echo "$ram < 6.0" | bc -l) )); then
    echo "qwen3:1.7b"
  else
    echo "qwen3:8b"
  fi
}

resolve_ollama_bin() {
  if command -v ollama >/dev/null 2>&1; then
    command -v ollama
    return
  fi
  # Known locations
  if [[ -f /usr/local/bin/ollama ]]; then echo /usr/local/bin/ollama
  elif [[ -f /usr/bin/ollama ]]; then echo /usr/bin/ollama
  elif [[ -f ~/.local/bin/ollama ]]; then echo ~/.local/bin/ollama
  elif [[ -f /opt/homebrew/bin/ollama ]]; then echo /opt/homebrew/bin/ollama
  elif [[ -x ~/AppData/Local/Programs/Ollama/ollama.exe ]]; then
    echo ~/AppData/Local/Programs/Ollama/ollama.exe
  fi
}

is_ollama_installed() {
  [[ -n "$(resolve_ollama_bin)" ]]
}

is_ollama_reachable() {
  if command -v curl >/dev/null 2>&1; then
    curl -s -m 2 "http://localhost:11434/api/tags" >/dev/null 2>&1
  elif command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=2 -O - "http://localhost:11434/api/tags" >/dev/null 2>&1
  else
    # Python fallback
    python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2)" >/dev/null 2>&1
  fi
}

has_ollama_model() {
  local model="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -s "http://localhost:11434/api/tags" 2>/dev/null | grep -q "$model" && return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import urllib.request, json; d=json.loads(urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2).read()); exit(0 if any('$model' in m.get('name','') for m in d.get('models',[])) else 1)" 2>/dev/null && return 0
  fi
  return 1
}

install_ollama() {
  step "Installing Ollama"
  if [[ "$OSTYPE" == "linux"* ]]; then
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL https://ollama.ai/install.sh | sh && ok "Ollama installed (Linux)" && return 0
    fi
  elif [[ "$OSTYPE" == "darwin"* ]]; then
    if command -v brew >/dev/null 2>&1; then
      brew install ollama && ok "Ollama installed (macOS)" && return 0
    else
      warn "Homebrew not found — install manually from https://ollama.ai/download/mac"
      return 1
    fi
  elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    if command -v winget >/dev/null 2>&1; then
      winget install --silent --accept-package-agreements Ollama.Ollama && ok "Ollama installed (Windows)" && return 0
    else
      warn "winget not found — install Ollama manually from https://ollama.ai/download/windows"
      return 1
    fi
  fi
  return 1
}

ensure_ollama_running() {
  step "Ensuring Ollama server is running"
  if is_ollama_reachable; then
    ok "Ollama is already running"
    return 0
  fi
  local ollama_bin
  ollama_bin="$(resolve_ollama_bin)"
  if [[ -z "$ollama_bin" ]]; then
    fail "Ollama binary not found"
    return 1
  fi
  case "$OSTYPE" in
    msys|cygwin|win32)
      # Windows: prefer desktop app, fall back to serve
      if [[ -x "~/AppData/Local/Programs/Ollama/ollama app.exe" ]]; then
        start "~/AppData/Local/Programs/Ollama/ollama app.exe" >/dev/null 2>&1 &
      fi
      "$ollama_bin" serve >/dev/null 2>&1 &
      ;;
    *)
      # POSIX: start serve detached
      nohup "$ollama_bin" serve >/dev/null 2>&1 &
      ;;
  esac

  # Wait for server to become reachable (up to 30s)
  local deadline=$((SECONDS + 30))
  while (( SECONDS < deadline )); do
    sleep 1
    if is_ollama_reachable; then
      ok "Ollama server is now running"
      return 0
    fi
  done
  fail "Ollama server did not become reachable in time"
  return 1
}

pull_model() {
  local model="$1"
  local ollama_bin
  ollama_bin="$(resolve_ollama_bin)"
  if [[ -z "$ollama_bin" ]]; then
    fail "Ollama binary not found (needed to pull $model)"
    return 1
  fi
  step "Pulling model: $model  (live progress below — one-time multi-GB download)"
  # Stream Ollama's native progress bar straight to the terminal (no capture/tail),
  # so the pull never looks frozen.
  if "$ollama_bin" pull "$model"; then
    ok "Model $model pulled successfully"
    return 0
  else
    fail "Failed to pull $model — run manually: $ollama_bin pull $model"
    return 1
  fi
}

# ── Main logic ────────────────────────────────────────────────────────────────

check_hermes() {
  step "Checking Hermes installation"
  local ram_gb
  ram_gb="$(get_available_ram_gb)"
  local model_wanted
  model_wanted="$(select_model_for_ram "$ram_gb")"

  printf "  RAM detected: %.1f GB\n" "$ram_gb"
  printf "  Model recommended: %s\n" "$model_wanted"

  if is_ollama_installed; then
    ok "Ollama is installed"
  else
    fail "Ollama is NOT installed"
    return 1
  fi

  if is_ollama_reachable; then
    ok "Ollama server is reachable"
  else
    warn "Ollama server is NOT running (check/repair can start it)"
    return 1
  fi

  if has_ollama_model "$model_wanted"; then
    ok "Model $model_wanted is installed"
    return 0
  else
    warn "Model $model_wanted is NOT installed (check/repair can pull it)"
    return 1
  fi
}

repair_hermes() {
  step "Repairing Hermes installation"
  local ram_gb
  ram_gb="$(get_available_ram_gb)"
  local model_wanted
  model_wanted="$(select_model_for_ram "$ram_gb")"

  if ! is_ollama_installed; then
    if ! install_ollama; then
      fail "Ollama installation failed"
      return 1
    fi
  fi

  if ! ensure_ollama_running; then
    fail "Could not start Ollama server"
    return 1
  fi

  if ! has_ollama_model "$model_wanted"; then
    if ! pull_model "$model_wanted"; then
      fail "Could not pull model $model_wanted"
      return 1
    fi
  fi

  ok "Hermes repair complete"
  return 0
}

install_hermes() {
  step "Installing Hermes (Ollama + model)"
  check_hermes && { ok "Hermes is already fully installed"; return 0; }
  repair_hermes
}

# ── Execute based on mode ─────────────────────────────────────────────────────

case "$MODE" in
  check)
    check_hermes
    exit $?
    ;;
  repair)
    repair_hermes
    exit $?
    ;;
  install)
    install_hermes
    exit $?
    ;;
esac
