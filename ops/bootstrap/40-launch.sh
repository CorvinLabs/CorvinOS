#!/usr/bin/env bash
# Phase 40 — launch the compose stack and wait for healthcheck green.

set -euo pipefail

CORVIN_PREFIX="${CORVIN_PREFIX:-/opt/corvin}"
CORVIN_REPO_DIR="${CORVIN_REPO_DIR:-/opt/corvin-repo}"

GREEN='\033[1;32m'; YELLOW='\033[1;33m'; RED='\033[1;31m'; NC='\033[0m'
ok()   { printf "${GREEN}  ✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}  ⚠${NC} %s\n" "$*"; }
fail() { printf "${RED}  ✗${NC} %s\n" "$*" >&2; exit 1; }

# Pre-flight: warn if .env still has empty critical keys.
set -a; . "$CORVIN_PREFIX/.env"; set +a
if [[ -z "${OPENAI_API_KEY:-}" && -z "${ANTHROPIC_API_KEY:-}" ]]; then
    warn "no API keys configured yet — stack will start but engine calls will fail."
    warn "edit $CORVIN_PREFIX/.env, then 'systemctl restart corvin-compose'."
fi

# DNS sanity check for the configured domain.
if [[ -n "${CORVIN_DOMAIN:-}" ]]; then
    resolved=$(dig +short "$CORVIN_DOMAIN" @1.1.1.1 2>/dev/null | tail -n1)
    public_ip=$(curl -s -4 --max-time 5 ifconfig.me 2>/dev/null || true)
    if [[ -z "$resolved" ]]; then
        warn "$CORVIN_DOMAIN does not resolve — Let's Encrypt will fail until DNS is set."
    elif [[ -n "$public_ip" && "$resolved" != "$public_ip" ]]; then
        warn "$CORVIN_DOMAIN resolves to $resolved (host is $public_ip) — Cloudflare proxy?"
        warn "For HTTP-01 ACME, set the DNS record to 'DNS only' (gray cloud)."
    else
        ok "$CORVIN_DOMAIN → $resolved (matches host)"
    fi
fi

systemctl start corvin-compose.service
ok "corvin-compose started"

echo "  → waiting up to 120s for healthcheck"
for i in $(seq 1 24); do
    state=$(docker inspect --format '{{.State.Health.Status}}' corvin 2>/dev/null || echo "no-such-container")
    case "$state" in
        healthy)    ok "container healthy"; exit 0 ;;
        starting)   sleep 5 ;;
        unhealthy)  fail "container reports unhealthy — check 'docker logs corvin'" ;;
        no-such-container) sleep 5 ;;
        *)          sleep 5 ;;
    esac
done
warn "healthcheck still pending after 120s — check 'docker compose -f $CORVIN_PREFIX/docker-compose.yml logs'"
