# Corvin on Docker — Headless Server Setup

Run Corvin completely in Docker with **zero host system dependencies** (except Docker itself). Includes web console UI, all bridges, REST API gateway, and persistent state management.

---

## What's Included

| Component | Port | Purpose |
|---|---|---|
| **Web Console** | 8765 (internal), 443 (external) | Browser UI for configuration + monitoring |
| **Gateway REST API** | 8000 (internal), 443 (external) | Programmatic run submission + webhook dispatch |
| **Bridge Daemons** | internal only | WhatsApp, Telegram, Discord, Slack, Email |
| **Adapter** | internal only | Main orchestrator + skill-forge/forge MCP server |
| **Metrics** | 9090 | Prometheus scrape endpoint |

The web console is **pre-built into the image** at `core/console/corvin_console/web-next/dist/` and served by the gateway under `/console/`.

---

## Prerequisites

- **Docker 24.0+** + docker-compose 2.20+
- **A Linux server** (Ubuntu 22.04+ / Debian 12+ recommended, or any Docker host)
- **1 GB RAM minimum** (4 GB comfortable), **2 GB disk**
- **API keys:**
  - `ANTHROPIC_API_KEY` for Claude (or mounted credentials)
  - `OPENAI_API_KEY` for Whisper STT + TTS (optional if using local providers)

---

## Quick Start (5 minutes)

### Option A: Automated Bootstrap (Recommended)

One command installs Docker, clones the repo, generates config, and starts everything:

```bash
sudo curl -fsSL https://raw.githubusercontent.com/veegee82/Corvin/main/ops/bootstrap/install.sh | bash
```

This creates:
- `/opt/corvin-repo/` — cloned + pinned to latest `v*` tag
- `/opt/corvin/` — persistent state (home, settings, backups)
- `corvin-compose.service` — systemd service for auto-start
- `corvin-audit-verify.timer` — nightly audit-chain verification

Then edit `/opt/corvin/.env` and restart:

```bash
sudo systemctl restart corvin-compose
```

---

### Option B: Manual Setup

#### 1. Create directories

```bash
sudo mkdir -p /opt/corvin/{home,bridge-settings,tls,backups}
sudo chmod 0700 /opt/corvin
```

#### 2. Clone and prepare

```bash
cd /opt/corvin
sudo git clone --depth=1 https://github.com/veegee82/Corvin.git repo
cd repo && sudo git fetch --tags && sudo git checkout $(git tag -l 'v*' --sort=-v:refname | head -n1)
```

#### 3. Create `.env` file

```bash
sudo cp ops/.env.template /opt/corvin/.env
sudo chmod 0600 /opt/corvin/.env
```

Edit `/opt/corvin/.env`:

```bash
# Image (or build locally: corvin:dev)
CORVIN_IMAGE=ghcr.io/veegee82/corvinos:latest

# Tenant ID (unique per deployment)
CORVIN_TENANT_ID=_default

# Required: Claude API key
ANTHROPIC_API_KEY=sk-ant-...

# Optional: OpenAI key for Whisper STT + TTS
OPENAI_API_KEY=sk-org-...

# Optional: Enable bridges (default all off)
CORVIN_BRIDGE_DISCORD=true
CORVIN_BRIDGE_TELEGRAM=true

# TLS: leave empty for self-signed, or set to your domain
CORVIN_DOMAIN=corvin.example.com
CORVIN_ACME_EMAIL=admin@example.com

# Directories
CORVIN_DATA_DIR=/opt/corvin/home
CORVIN_BRIDGE_SETTINGS_DIR=/opt/corvin/bridge-settings

# Resources
CORVIN_MEM_LIMIT=4G
```

#### 4. Create docker-compose.yml

```bash
sudo cp ops/docker-compose.yml /opt/corvin/docker-compose.yml
```

#### 5. Create Caddyfile (for TLS)

```bash
sudo cp ops/Caddyfile.template /opt/corvin/Caddyfile
```

For **self-signed certificates** (development):

```bash
cd /opt/corvin/tls
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout cert.key -out cert.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
```

#### 6. Create bridge settings (optional)

```bash
sudo mkdir -p /opt/corvin/bridge-settings

# For Discord:
cat | sudo tee /opt/corvin/bridge-settings/discord.json > /dev/null <<'EOF'
{
  "enabled": true,
  "discord_token": "YOUR_TOKEN_HERE",
  "whitelist": [123456789],
  "chat_profiles": {}
}
EOF

# For Telegram (similar structure):
cat | sudo tee /opt/corvin/bridge-settings/telegram.json > /dev/null <<'EOF'
{
  "enabled": true,
  "telegram_token": "YOUR_TOKEN_HERE",
  "whitelist": [123456789],
  "chat_profiles": {}
}
EOF

# For WhatsApp (configure after first start via QR-code):
echo '{"enabled": true}' | sudo tee /opt/corvin/bridge-settings/whatsapp.json
echo '{"enabled": true}' | sudo tee /opt/corvin/bridge-settings/slack.json
echo '{"enabled": true}' | sudo tee /opt/corvin/bridge-settings/email.json
```

#### 7. Fix permissions

```bash
sudo chown -R 1000:1000 /opt/corvin/home
sudo chmod 0600 /opt/corvin/.env
```

#### 8. Start the stack

```bash
sudo docker compose -f /opt/corvin/docker-compose.yml --env-file /opt/corvin/.env up -d
```

Wait for healthcheck (30–60 seconds):

```bash
sudo docker logs -f corvin
```

---

## Accessing the Web Console

### Local network (Tailscale — recommended for private access)

Add a Tailscale sidecar to the compose stack (see `ops/tailscale/README.md`):

```yaml
tailscale:
  image: ghcr.io/tailscale/tailscale:latest
  cap_add: [NET_ADMIN, SYS_MODULE]
  volumes:
    - tailscale-state:/var/lib/tailscale
  environment:
    TS_AUTHKEY: ${TAILSCALE_AUTHKEY}  # pre-auth key from tailscale.com
```

Then access from any device on your Tailnet: `https://corvin-hostname/console/`

### Public internet (Let's Encrypt — requires domain)

Edit `/opt/corvin/.env`:

```bash
CORVIN_DOMAIN=corvin.example.com
CORVIN_ACME_EMAIL=admin@example.com
```

**Prerequisites:**
- DNS A-record already resolves to this host
- Firewall allows 80/443 inbound

Caddy auto-provisions a Let's Encrypt cert on first request.

Access: `https://corvin.example.com/console/`

### Localhost (SSH tunnel — for testing)

```bash
ssh -L 8765:localhost:8765 user@host
# Then: https://localhost:8765/console/
```

---

## Configuration

### Enable/disable bridges

Edit `/opt/corvin/.env`:

```bash
CORVIN_BRIDGE_DISCORD=true    # enables bridge-discord daemon
CORVIN_BRIDGE_TELEGRAM=false  # skips bridge-telegram
CORVIN_BRIDGE_WHATSAPP=true
```

Then restart:

```bash
sudo systemctl restart corvin-compose
# or: sudo docker compose -f /opt/corvin/docker-compose.yml restart corvin
```

### Hot-reload bridge settings

Bridge daemons **re-read** `settings.json` on every message — no restart needed:

```bash
# Edit a token or whitelist:
sudo nano /opt/corvin/bridge-settings/discord.json
# Takes effect on next message
```

### Add storage for large datasets

Bind-mount an EBS / NFS volume:

Edit `/opt/corvin/docker-compose.yml`:

```yaml
services:
  corvin:
    volumes:
      - /mnt/data:/home/corvin/data  # large volume
```

Then restart.

---

## Persistent State Layout

```
/opt/corvin/
├── .env                                  (secrets, mode 0600)
├── docker-compose.yml
├── Caddyfile
├── tls/                                  (TLS certs)
│   ├── cert.pem
│   └── cert.key
├── home/                                 (bind-mount → /home/corvin)
│   └── .corvin/                         (tenant tree)
│       ├── tenants/_default/
│       │   ├── global/                   (audit chain, vault, roles, quota)
│       │   ├── sessions/                 (chat-scoped forge/skills)
│       │   ├── forge/                    (runtime tools)
│       │   ├── skill-forge/              (runtime skills)
│       │   ├── voice/                    (voice state, listener profile)
│       │   └── cowork/                   (personas)
│       ├── global → tenants/_default/global  (symlink)
│       └── sessions → tenants/_default/sessions
├── bridge-settings/                      (bind-mount, read-only)
│   ├── discord.json
│   ├── telegram.json
│   ├── whatsapp.json
│   ├── slack.json
│   └── email.json
└── backups/                              (timestamped tarballs)
```

**Backup & restore:**

```bash
# Backup
tar -czf /opt/corvin/backups/home-$(date +%F).tgz -C /opt/corvin home

# Restore
cd /opt/corvin && tar -xzf backups/home-2026-05-19.tgz
```

---

## Operations

### Status

```bash
sudo docker compose -f /opt/corvin/docker-compose.yml ps
```

### Logs

```bash
# All services
sudo docker logs -f corvin

# Specific service
sudo docker exec corvin tail -f /var/log/corvin/adapter.log
sudo docker exec corvin tail -f /var/log/corvin/gateway.log
```

### Restart

```bash
sudo systemctl restart corvin-compose
# or
sudo docker compose -f /opt/corvin/docker-compose.yml restart
```

### Verify audit chain integrity

```bash
sudo docker exec corvin \
  /opt/corvin-repo/operator/voice/scripts/voice-audit.py verify
```

### Shell into container

```bash
sudo docker exec -it corvin bash
```

### Update to latest tag

```bash
cd /opt/corvin/repo
sudo git fetch --tags
sudo git checkout $(git tag -l 'v*' --sort=-v:refname | head -n1)
sudo systemctl restart corvin-compose
```

---

## Troubleshooting

### Web console returns 404

The console UI is mounted at `/console/`. Access it at:
- `http://localhost:8765/console/` (direct to gateway)
- `https://corvin.example.com/console/` (through Caddy)

If still 404, check the gateway logs:

```bash
sudo docker exec corvin tail /var/log/corvin/gateway.log | grep console
```

### Bridge not starting

Check that the env var is set and the settings file exists:

```bash
# Verify env var is true
sudo docker exec corvin env | grep CORVIN_BRIDGE_DISCORD

# Check settings file mounted correctly
sudo docker exec corvin ls -la /opt/corvin-repo/operator/bridges/discord/settings.json

# Check supervisord status
sudo docker exec corvin supervisorctl status
```

### "Healthcheck failed"

Wait 2–3 minutes on first start (all services booting). Then check logs:

```bash
sudo docker logs corvin | head -50
sudo docker exec corvin /usr/local/bin/corvin-healthcheck
```

### TLS cert not renewing

Check Caddy logs:

```bash
sudo docker logs corvin-caddy | grep -i acme
```

Ensure DNS A-record resolves correctly and ports 80/443 are open.

### Bridge daemon keeps restarting

Check the daemon's log:

```bash
sudo docker exec corvin tail /var/log/corvin/bridge-discord.log
```

Common issues:
- Invalid token in `settings.json`
- Wrong whitelist format (should be array of integers)
- Missing required fields in settings

---

## Advanced: Building Your Own Image

To build locally from source:

```bash
cd /opt/corvin/repo
sudo docker build \
  -t corvin:dev \
  -f ops/Dockerfile \
  .
```

Then update `/opt/corvin/.env`:

```bash
CORVIN_IMAGE=corvin:dev
```

And restart:

```bash
sudo systemctl restart corvin-compose
```

---

## Architecture Overview

```
┌─ Host ──────────────────────────────────────────────┐
│                                                      │
│  /opt/corvin/                                       │
│  ├── .env (secrets)                                 │
│  ├── home/ ──────────────┐                          │
│  └── bridge-settings/ ───┤                          │
│                          │                          │
└──────────────────────────┼──────────────────────────┘
                           │
                      bind mounts
                           │
┌─ Docker ─────────────────┼──────────────────────────┐
│                          │                          │
│  ┌──────────────────────────────────────────────┐  │
│  │ Container: corvin                           │  │
│  │ ──────────────────────────────────────────── │  │
│  │                                              │  │
│  │  supervisord (root) ────────────────────┐   │  │
│  │  ├─ adapter        (corvin user)       │   │  │
│  │  │  └─ ForgeServer + SkillForgeServer  │   │  │
│  │  ├─ gateway        (corvin user)       │   │  │
│  │  │  └─ Console UI mounted at /console/ │   │  │
│  │  ├─ bridge-discord (corvin user)       │   │  │
│  │  ├─ bridge-telegram (corvin user)      │   │  │
│  │  └─ ... more bridges (opt-in via env)  │   │  │
│  │                                          │   │  │
│  │  /home/corvin/                          │   │  │
│  │  └─ .corvin/                            │   │  │
│  │      ├─ tenants/_default/               │   │  │
│  │      │  ├─ global/      (audit chain)   │   │  │
│  │      │  ├─ sessions/    (chat scope)    │   │  │
│  │      │  ├─ forge/       (tools)         │   │  │
│  │      │  └─ skill-forge/ (skills)        │   │  │
│  │      └─ symlinks (compat)               │   │  │
│  │                                          │   │  │
│  │  :8000 (gateway) ──────────────────────┘   │  │
│  └──────────────────────────────────────────────┘  │
│                                                  │  │
│  ┌──────────────────────────────────────────────┐  │
│  │ Container: caddy (TLS reverse proxy)         │  │
│  │ ──────────────────────────────────────────── │  │
│  │  :80  (ACME HTTP-01 challenge + redirect)   │  │
│  │  :443 (public HTTPS)                        │  │
│  │  ↓                                          │  │
│  │  corvin:8000 (internal gateway)            │  │
│  └──────────────────────────────────────────────┘  │
│                                                  │  │
└──────────────────────────────────────────────────┘
        ↓
   Network: corvin-edge
```

---

## Multi-tenant Deployments

For multiple independent Corvin instances (e.g., different teams):

```bash
# Deploy instance 1
CORVIN_TENANT_ID=team-a \
  docker compose -f /opt/corvin/docker-compose.yml up -d

# Deploy instance 2 (on a different host or port)
CORVIN_TENANT_ID=team-b \
  CORVIN_DOMAIN=corvin-team-b.example.com \
  docker compose -f /opt/corvin/docker-compose.yml up -d
```

Each tenant:
- Has its own audit chain (independent, verifiable)
- Its own workspace (forge tools, skills, vault)
- Its own roles, consent, and quota
- Cannot access other tenants' data

---

## License & Support

- **License:** Apache 2.0
- **Issues:** https://github.com/veegee82/Corvin/issues
- **Documentation:** https://github.com/veegee82/Corvin/blob/main/docs/

For EU AI Act + GDPR compliance details, see `docs/audit-and-compliance.md`.
