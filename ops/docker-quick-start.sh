#!/usr/bin/env bash
# Corvin Docker Quick-Start — Manual Setup
#
# Usage:
#   chmod +x docker-quick-start.sh
#   ./docker-quick-start.sh
#
# Sets up /opt/corvin with all directories, configs, and systemd service.
# Requires: docker, docker-compose, git, openssl

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}ℹ${NC} $*"; }
log_ok() { echo -e "${GREEN}✓${NC} $*"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $*"; }
log_err() { echo -e "${RED}✗${NC} $*"; exit 1; }

# Check prerequisites
log_info "Checking prerequisites..."

[[ $(id -u) == 0 ]] || log_err "Must run as root (or use sudo)"

command -v docker &> /dev/null || log_err "Docker not found. Install from https://docs.docker.com/engine/install/"
# Support both docker-compose v1 and v2 (docker compose)
{ command -v docker-compose &> /dev/null || docker compose version &> /dev/null; } || log_err "docker-compose not found. Install v2: https://docs.docker.com/compose/install/"
command -v git &> /dev/null || log_err "git not found"
command -v openssl &> /dev/null || log_err "openssl not found"

log_ok "Prerequisites OK (docker, git, openssl)"

# Directories
CORVIN_HOME="/opt/corvin"
CORVIN_REPO="${CORVIN_HOME}/repo"

log_info "Setting up directories..."

mkdir -p \
  "${CORVIN_HOME}/home" \
  "${CORVIN_HOME}/bridge-settings" \
  "${CORVIN_HOME}/tls" \
  "${CORVIN_HOME}/backups"

log_ok "Directories created: ${CORVIN_HOME}"

# Clone repo
if [[ ! -d "${CORVIN_REPO}" ]]; then
  log_info "Cloning Corvin repository..."
  git clone https://github.com/veegee82/Corvin.git "${CORVIN_REPO}"
  cd "${CORVIN_REPO}"
  git fetch --tags
  git fetch origin 'refs/heads/*:refs/heads/*'
  LATEST_TAG=$(git tag -l 'v*' --sort=-v:refname | head -n1)
  git checkout "${LATEST_TAG}"
  log_ok "Cloned and checked out ${LATEST_TAG}"
else
  log_warn "Repo already exists at ${CORVIN_REPO}. Skipping clone."
fi

# .env file
if [[ ! -f "${CORVIN_HOME}/.env" ]]; then
  log_info "Creating .env from template..."
  cp "${CORVIN_REPO}/ops/.env.template" "${CORVIN_HOME}/.env"
  chmod 0600 "${CORVIN_HOME}/.env"
  log_warn "⚠ IMPORTANT: Edit ${CORVIN_HOME}/.env and set:"
  echo "   - ANTHROPIC_API_KEY (required)"
  echo "   - OPENAI_API_KEY (optional, for STT/TTS)"
  echo "   - CORVIN_DOMAIN, CORVIN_ACME_EMAIL (for TLS)"
  echo "   - Bridge toggles: CORVIN_BRIDGE_DISCORD=true, etc."
else
  log_warn ".env already exists. Skipping."
fi

# docker-compose.yml
if [[ ! -f "${CORVIN_HOME}/docker-compose.yml" ]]; then
  log_info "Copying docker-compose.yml..."
  cp "${CORVIN_REPO}/ops/docker-compose.yml" "${CORVIN_HOME}/docker-compose.yml"
  log_ok "docker-compose.yml ready"
else
  log_warn "docker-compose.yml already exists. Skipping."
fi

# Caddyfile
if [[ ! -f "${CORVIN_HOME}/Caddyfile" ]]; then
  log_info "Copying Caddyfile..."
  cp "${CORVIN_REPO}/ops/Caddyfile.template" "${CORVIN_HOME}/Caddyfile"
  log_ok "Caddyfile ready"
else
  log_warn "Caddyfile already exists. Skipping."
fi

# Self-signed TLS cert
if [[ ! -f "${CORVIN_HOME}/tls/cert.pem" ]]; then
  log_info "Generating self-signed TLS certificate..."
  cd "${CORVIN_HOME}/tls"
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout cert.key -out cert.pem \
    -subj "/CN=localhost" \
    -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
  chmod 0600 cert.key cert.pem
  log_ok "Self-signed cert at ${CORVIN_HOME}/tls/"
else
  log_warn "TLS certs already exist. Skipping."
fi

# Bridge settings (empty templates)
for bridge in discord telegram whatsapp slack email; do
  SETTINGS="${CORVIN_HOME}/bridge-settings/${bridge}.json"
  if [[ ! -f "${SETTINGS}" ]]; then
    cat > "${SETTINGS}" <<EOF
{
  "enabled": false,
  "_comment": "Set enabled: true, add token and whitelist, then restart container"
}
EOF
    log_ok "Created ${SETTINGS} (edit as needed)"
  fi
done

# Permissions
log_info "Setting permissions..."
chown -R 1000:1000 "${CORVIN_HOME}/home"
chmod 0600 "${CORVIN_HOME}/.env"
log_ok "Permissions set"

# systemd service
if [[ ! -f /etc/systemd/system/corvin-compose.service ]]; then
  log_info "Installing systemd service..."
  cat > /etc/systemd/system/corvin-compose.service <<EOF
[Unit]
Description=Corvin Docker Compose Stack
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=exec
WorkingDirectory=${CORVIN_HOME}
ExecStart=/usr/bin/docker compose -f docker-compose.yml --env-file .env up
ExecStop=/usr/bin/docker compose -f docker-compose.yml down
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=corvin

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  log_ok "systemd service installed: corvin-compose.service"
else
  log_warn "Service already exists. Skipping."
fi

# Summary
echo ""
echo "════════════════════════════════════════════════════════════════"
log_ok "Corvin Docker setup complete!"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "📋 Next steps:"
echo ""
echo "1. Edit configuration:"
echo "   sudo nano ${CORVIN_HOME}/.env"
echo ""
echo "   Required:"
echo "   • ANTHROPIC_API_KEY=sk-ant-..."
echo ""
echo "   Optional:"
echo "   • OPENAI_API_KEY=sk-org-..."
echo "   • CORVIN_DOMAIN=corvin.example.com"
echo "   • CORVIN_ACME_EMAIL=admin@example.com"
echo "   • CORVIN_BRIDGE_DISCORD=true"
echo "   • CORVIN_BRIDGE_TELEGRAM=true"
echo ""
echo "2. Start the stack:"
echo "   sudo systemctl start corvin-compose"
echo ""
echo "3. Monitor startup (wait 30–60 sec):"
echo "   sudo docker logs -f corvin"
echo ""
echo "4. Access console:"
echo "   • Localhost:       http://localhost:8765/console/"
echo "   • Public domain:   https://corvin.example.com/console/"
echo "   • SSH tunnel:      ssh -L 8765:localhost:8765 user@host"
echo ""
echo "5. Enable on boot:"
echo "   sudo systemctl enable corvin-compose"
echo ""
echo "📁 File layout:"
echo "   ${CORVIN_HOME}/.env           (secrets, keep private)"
echo "   ${CORVIN_HOME}/home/          (persistent state, bind-mount)"
echo "   ${CORVIN_HOME}/bridge-settings/ (bridge configs)"
echo "   ${CORVIN_HOME}/tls/           (TLS certificates)"
echo ""
echo "📖 Full documentation:"
echo "   See DOCKER_HEADLESS_SETUP.md in the outputs folder"
echo ""
echo "🆘 Troubleshooting:"
echo "   sudo docker compose -f ${CORVIN_HOME}/docker-compose.yml logs"
echo "   sudo docker exec corvin supervisorctl status"
echo ""
