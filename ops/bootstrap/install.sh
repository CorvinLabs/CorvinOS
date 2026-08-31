#!/usr/bin/env bash
# Corvin one-shot installer.
#
# Designed to be piped from curl so a fresh box can come up with zero
# local-file expectations:
#
#   curl -fsSL https://raw.githubusercontent.com/veegee82/Corvin/main/ops/bootstrap/install.sh | sudo bash
#
# Or with overrides:
#
#   CORVIN_REPO_URL=https://github.com/your/fork.git \
#   CORVIN_BRANCH=feature \
#   curl -fsSL .../install.sh | sudo bash
#
# Idempotent: re-runs pull the latest tag and re-execute every phase. Each
# phase script is itself idempotent (skips already-done steps).

set -euo pipefail

CORVIN_REPO_URL="${CORVIN_REPO_URL:-https://github.com/veegee82/Corvin.git}"
CORVIN_BRANCH="${CORVIN_BRANCH:-main}"
CORVIN_PREFIX="${CORVIN_PREFIX:-/opt/corvin}"
CORVIN_REPO_DIR="${CORVIN_REPO_DIR:-/opt/corvin-repo}"

GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; CYAN='\033[1;36m'; NC='\033[0m'
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
fail() { printf "${RED}✗${NC} %s\n" "$*" >&2; exit 1; }
step() { printf "\n${CYAN}══ %s ══${NC}\n" "$*"; }

# ── preflight ────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || fail "must run as root (pipe through 'sudo bash')"
[[ -f /etc/os-release ]] || fail "/etc/os-release missing — unsupported OS"
. /etc/os-release
case "$ID" in
    ubuntu|debian) ok "host: $PRETTY_NAME" ;;
    *)             fail "unsupported distro: $ID (need Ubuntu or Debian)" ;;
esac

# ── fetch repo (we need the bootstrap scripts before we can prep) ────────
step "Fetching Corvin repo → $CORVIN_REPO_DIR"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates >/dev/null
if [[ -d "$CORVIN_REPO_DIR/.git" ]]; then
    git -C "$CORVIN_REPO_DIR" fetch --quiet --tags origin "$CORVIN_BRANCH"
    git -C "$CORVIN_REPO_DIR" checkout --quiet "$CORVIN_BRANCH"
    git -C "$CORVIN_REPO_DIR" pull --ff-only --quiet
    ok "updated existing checkout"
else
    git clone --branch "$CORVIN_BRANCH" --quiet "$CORVIN_REPO_URL" "$CORVIN_REPO_DIR"
    ok "cloned $CORVIN_REPO_URL"
fi

# ── run nested phases ────────────────────────────────────────────────────
BOOTSTRAP_DIR="$CORVIN_REPO_DIR/ops/bootstrap"
export CORVIN_PREFIX CORVIN_REPO_DIR

for phase in 10-system-prep 20-fetch-repo 30-tenant-init 40-launch; do
    script="$BOOTSTRAP_DIR/${phase}.sh"
    [[ -x "$script" ]] || chmod +x "$script" 2>/dev/null || true
    [[ -f "$script" ]] || fail "missing phase script: $script"
    step "Phase: $phase"
    bash "$script"
done

step "Done"
ok "Corvin provisioned at $CORVIN_PREFIX"
echo
echo "Next steps:"
echo "  1. Edit ${CORVIN_PREFIX}/.env to fill ANTHROPIC_API_KEY, OPENAI_API_KEY,"
echo "     CORVIN_DOMAIN, CORVIN_ACME_EMAIL, and any bridge tokens you want."
echo "  2. Restart the stack:   systemctl restart corvin-compose"
echo "  3. Check status:        docker compose -f ${CORVIN_REPO_DIR}/ops/docker-compose.yml ps"
