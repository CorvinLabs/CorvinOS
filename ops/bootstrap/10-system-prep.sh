#!/usr/bin/env bash
# Phase 10 — system prep: packages, swap, UFW, fail2ban, /opt/corvin layout.
#
# Idempotent. Safe to re-run.

set -euo pipefail

CORVIN_PREFIX="${CORVIN_PREFIX:-/opt/corvin}"
SWAP_SIZE_MB="${CORVIN_SWAP_MB:-2048}"

GREEN='\033[1;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { printf "${GREEN}  ✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}  ⚠${NC} %s\n" "$*"; }

# ── packages ─────────────────────────────────────────────────────────────
echo "  → installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq \
    ca-certificates curl wget gnupg lsb-release \
    git jq age \
    ufw fail2ban \
    dnsutils net-tools \
    >/dev/null
ok "base packages present"

# ── docker (official repo) ───────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "  → installing Docker CE"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin >/dev/null
    systemctl enable --now docker >/dev/null
    ok "Docker CE installed"
else
    ok "Docker already present ($(docker --version))"
fi

# ── swap ─────────────────────────────────────────────────────────────────
if ! swapon --show | grep -q .; then
    echo "  → creating ${SWAP_SIZE_MB} MB swap"
    fallocate -l "${SWAP_SIZE_MB}M" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    ok "swap active"
else
    ok "swap already configured ($(swapon --show=NAME --noheadings | head -1))"
fi

# ── UFW ──────────────────────────────────────────────────────────────────
echo "  → configuring UFW"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp comment "ssh" >/dev/null
ufw allow 80/tcp comment "acme-http-01" >/dev/null
ufw allow 443/tcp comment "corvin-console" >/dev/null
ufw --force enable >/dev/null
ok "UFW: 22, 80, 443 open"
warn "Tighten 443 later: 'ufw allow from <your-cidr> to any port 443'"

# ── fail2ban ─────────────────────────────────────────────────────────────
systemctl enable --now fail2ban >/dev/null
ok "fail2ban enabled"

# ── layout under /opt/corvin ────────────────────────────────────────────
mkdir -p \
    "$CORVIN_PREFIX" \
    "$CORVIN_PREFIX/home" \
    "$CORVIN_PREFIX/data" \
    "$CORVIN_PREFIX/secrets" \
    "$CORVIN_PREFIX/backups"
chmod 0750 "$CORVIN_PREFIX/secrets" "$CORVIN_PREFIX/backups"

# Container runs as uid 1000 — bind-mounted home must match.
chown -R 1000:1000 "$CORVIN_PREFIX/home" "$CORVIN_PREFIX/data"
ok "layout: $CORVIN_PREFIX/{home,data,secrets,backups}"
