# Corvin Docker Setup Guide

## Executive Summary

Corvin ships with **production-ready Docker containerization** as an alternative to systemd. The Docker setup bundles all services (adapter, bridges, gateway, metrics, web console) into a single `corvin` container orchestrated by `docker-compose`, with optional Caddy reverse proxy for TLS.

**Key advantage over systemd:** No host-level system configuration needed. Single bind-mount of persistent state (`/opt/corvin/home` → `/home/corvin` inside container) means the entire Corvin instance is portable, backupable, and runs identically across Linux/macOS.

---

## Architecture Overview

### Current Systemd Setup
```
Host system:
  ~/.config/systemd/user/
    ├── corvin-voice-bridge-adapter.service
    ├── corvin-voice-bridge-discord.service
    ├── corvin-voice-bridge-telegram.service
    ├── corvin-voice-bridge-whatsapp.service
    ├── corvin-voice-bridge-slack.service
    └── corvin-voice-bridge-email.service

  ~/.corvin/  (persistent state)
    ├── tenants/_default/{global,sessions,forge,skill-forge,voice,cowork}
    ├── logs/
    └── run/

  bridge.sh (orchestrates systemd units or foreground mode)
```

### Docker Setup
```
Docker host:
  /opt/corvin/
    ├── docker-compose.yml          (generated from ops/docker-compose.yml)
    ├── Caddyfile                   (reverse proxy + TLS)
    ├── .env                        (secrets: API keys, bridge toggles)
    ├── home/                       (bind-mount → /home/corvin inside container)
    ├── bridge-settings/            (bind-mount: discord.json, telegram.json, etc.)
    └── tls/                        (self-signed or LetsEncrypt certs)

  Container (corvin:latest):
    ├── supervisord                 (process manager; starts adapter + bridges)
    ├── /home/corvin               (bind-mounted from host /opt/corvin/home)
    ├── /opt/corvin-repo           (code, copied at image-build time)
    ├── /opt/corvin-venv           (Python venv with all deps)
    └── /var/log/corvin            (logs)

  Caddy container (reverse proxy):
    ├── TLS termination
    ├── HTTP → HTTPS redirect
    └── Routes /console/ → corvin:8000
```

---

## Prerequisites

### Hardware
- **Minimum:** 2 GB RAM, 5 GB disk
- **Recommended:** 4+ GB RAM, 20+ GB disk for long audit chains

### Software
- **Docker:** 20.10+ (with BuildKit enabled, recommended)
- **Docker Compose:** 2.0+ (includes the `docker compose` command)
- **Host OS:** Linux (any distro with systemd or without), macOS (with Docker Desktop)

### For Windows
Use WSL2 (Windows Subsystem for Linux 2) with Ubuntu 20.04+, then install Docker Desktop for Windows.

---

## Step 1: Initial Setup (One-Shot)

Choose your deployment style:

### Option A: Quick Bootstrap (Recommended)
```bash
# Run as root (not necessary in Docker, but original script expects it)
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/veegee82/Corvin/main/ops/bootstrap/install.sh | bash'
```

This does:
1. Installs Docker, docker-compose, git, openssl
2. Clones Corvin repo to `/opt/corvin-repo`, pins to latest version tag
3. Creates `/opt/corvin/` with all directories and template files
4. Installs `corvin-compose.service` (systemd service to manage docker-compose)
5. Starts the stack and waits for healthcheck

### Option B: Manual Setup (For Development or Custom Paths)

```bash
# 1. Clone the repo (or use your local checkout)
git clone https://github.com/veegee82/Corvin.git ~/Corvin
cd ~/Corvin

# 2. Prepare host directories
mkdir -p /opt/corvin/{home,bridge-settings,tls}

# 3. Copy template files
cp ops/.env.template /opt/corvin/.env
cp ops/Caddyfile.template /opt/corvin/Caddyfile
cp ops/docker-compose.yml /opt/corvin/

# 4. Generate bridge settings stubs (one per enabled channel)
for ch in discord telegram whatsapp slack email; do
  cat > /opt/corvin/bridge-settings/$ch.json <<EOF
{
  "enabled": false,
  "_comment": "Set enabled: true, add token/credentials, restart container"
}
EOF
done

# 5. Generate self-signed TLS cert
mkdir -p /opt/corvin/tls
cd /opt/corvin/tls
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout cert.key -out cert.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
chmod 0600 cert.key cert.pem
cd -

# 6. Set permissions
chmod 0600 /opt/corvin/.env
chmod 0644 /opt/corvin/docker-compose.yml /opt/corvin/Caddyfile
chown -R 1000:1000 /opt/corvin/home
```

---

## Step 2: Configuration

### Edit `/opt/corvin/.env`
This file holds all secrets and toggles. **Keep it mode 0600 (readable by owner only).**

```bash
nano /opt/corvin/.env
```

**Required:**
```bash
# Your Claude API key (from https://console.anthropic.com)
ANTHROPIC_API_KEY=sk-ant-...
```

**Optional but recommended:**
```bash
# For voice STT/TTS (Whisper API)
OPENAI_API_KEY=sk-org-...

# For public domain + auto TLS via Let's Encrypt
CORVIN_DOMAIN=corvin.example.com
CORVIN_ACME_EMAIL=admin@example.com

# Enable specific bridges (default: all false)
CORVIN_BRIDGE_DISCORD=true
CORVIN_BRIDGE_TELEGRAM=true
CORVIN_BRIDGE_WHATSAPP=false
CORVIN_BRIDGE_SLACK=false
CORVIN_BRIDGE_EMAIL=false

# Memory limit (default 4G)
CORVIN_MEM_LIMIT=8G

# Debug logging (default 0)
CORVIN_DEBUG=1
```

### Bridge-Specific Configuration

Each enabled bridge needs a `settings.json` under `/opt/corvin/bridge-settings/`. The container bind-mounts these into the image so the daemon sees them as repo-local files.

**Discord example** (`/opt/corvin/bridge-settings/discord.json`):
```json
{
  "enabled": true,
  "token": "YOUR_DISCORD_BOT_TOKEN",
  "whitelist": [123456789, 987654321]
}
```

**Telegram example** (`/opt/corvin/bridge-settings/telegram.json`):
```json
{
  "enabled": true,
  "token": "YOUR_TELEGRAM_BOT_TOKEN",
  "whitelist": [123456789]
}
```

**WhatsApp** requires pairing on first start — check `docker logs corvin` for the QR code.

### Edit `/opt/corvin/Caddyfile` (Optional)

The default template works for localhost (no TLS). For a public domain:

```bash
nano /opt/corvin/Caddyfile
```

Replace `localhost` with your domain. If using Let's Encrypt:
```
corvin.example.com {
    reverse_proxy localhost:8000
    # Caddy auto-provisions TLS via HTTP-01 challenge
}
```

For Cloudflare-proxied DNS (proxy mode), use DNS-01 challenge — see comments in the template.

---

## Step 3: Build the Docker Image (Optional)

The docker-compose.yml pulls a pre-built image from GitHub Container Registry by default:
```yaml
image: ${CORVIN_IMAGE:-ghcr.io/veegee82/corvinos:latest}
```

To build locally from your repo checkout:

```bash
cd ~/Corvin
docker build -t corvinos:latest \
  --build-arg CORVIN_REPO_REF=main \
  -f ops/Dockerfile .

# Then override in docker-compose
export CORVIN_IMAGE=corvinos:latest
```

**Build time:** 5–10 minutes (includes Node.js deps, Python venv, console UI build).

---

## Step 4: Start the Stack

### Using docker-compose directly (for testing)
```bash
cd /opt/corvin
docker compose --env-file .env up -d
```

Check status:
```bash
docker compose ps
docker logs corvin -f
```

### Using systemd service (for production)
```bash
sudo systemctl start corvin-compose
sudo systemctl enable corvin-compose  # Auto-restart on reboot
sudo systemctl status corvin-compose
```

View logs:
```bash
sudo journalctl -u corvin-compose -f
```

---

## Step 5: First-Run Access

When the container starts, the entrypoint generates a **one-time gateway token** (if no token exists):

```
╔══════════════════════════════════════════════════════════╗
║  Corvin — First-Run Setup                            ║
║                                                         ║
║  Console access token (shown ONCE — save it now):      ║
║                                                         ║
║  token_abc123def456...                                  ║
║                                                         ║
║  Open http://localhost:8000/console and paste it there. ║
╚══════════════════════════════════════════════════════════╝
```

Open the console:
- **Local:** `http://localhost:8000/console`
- **Public domain:** `https://corvin.example.com/console`
- **SSH tunnel:** `ssh -L 8000:localhost:8000 user@host`, then `http://localhost:8000/console`

Paste the token and press Enter. You now have admin access.

---

## Step 6: Configure Bridges

Once logged in to the console, you can:
1. Enable/disable bridges
2. Add chat profiles
3. View audit logs
4. Manage users and permissions

Or edit `/opt/corvin/bridge-settings/*.json` and restart:
```bash
docker compose restart corvin
```

---

## Operations

### View Status
```bash
docker compose ps
docker compose logs corvin [n]  # last n lines
docker compose logs -f           # live tail
```

### Restart Services
```bash
# Graceful restart
docker compose restart corvin

# Hard restart (container kill + rebuild)
docker compose down && docker compose up -d
```

### Check Health
```bash
curl http://localhost:8765/healthz
docker healthcheck
```

### View Audit Chain
```bash
docker exec corvin \
  /opt/corvin-repo/operator/voice/scripts/voice-audit.py verify
```

### Backup State
```bash
tar -czf /opt/corvin/backups/home-$(date +%F).tgz \
  -C /opt/corvin home
```

### Update to Latest Release
```bash
cd /opt/corvin-repo
sudo git fetch --tags
sudo git checkout $(git tag -l 'v*' --sort=-v:refname | head -n1)
docker compose down
docker compose up -d --build
```

---

## Key Files & Structure

```
/opt/corvin/
├── docker-compose.yml              ← compose definition (read-only after init)
├── Caddyfile                       ← reverse proxy config
├── .env                            ← secrets (mode 0600)
├── home/                           ← persistent container state (bind-mount)
│   └── .corvin/
│       ├── tenants/_default/
│       │   ├── global/             ← audit chain, vault, auth tokens
│       │   ├── sessions/           ← per-chat session state
│       │   ├── forge/              ← generated tools
│       │   ├── skill-forge/        ← generated skills
│       │   └── voice/              ← bridge daemons state
│       ├── .claude/                ← Claude Code config
│       └── logs/                   ← application logs
├── bridge-settings/                ← read-only bind-mounts
│   ├── discord.json
│   ├── telegram.json
│   ├── whatsapp.json
│   ├── slack.json
│   └── email.json
├── tls/                            ← TLS certs
│   ├── cert.pem
│   └── cert.key
└── backups/                        ← timestamped home/ tarballs
```

---

## Volumes & Bind-Mounts

The compose file maps:

| Host Path | Container Path | Purpose | Mount Mode |
|---|---|---|---|
| `/opt/corvin/home` | `/home/corvin` | Persistent state (everything) | rw |
| `/opt/corvin/bridge-settings/*` | `/opt/corvin-repo/operator/bridges/*/settings.json` | Bridge config | ro |
| (optional) `~/Corvin` | `/opt/corvin-repo` | Live development | ro |

---

## Networking

### Internal (docker-compose)
- `corvin` container exposes port `8000` (gateway/console)
- `caddy` container exposes ports `80`, `443` (public)
- Both join network `corvin-edge` (internal)

### Host-Level
- **Localhost:** `localhost:8000/console` (direct, no Caddy)
- **Public:** `https://corvin.example.com/console` (via Caddy)
- **SSH tunnel:** Port-forward `8000` to access from remote

### Port Allocation
```
Host          Container         Service
80/443  →  caddy    →  corvin:8000    (gateway + console UI)
                     9090             (Prometheus metrics, internal only)
```

---

## Systemd Service (Optional)

For auto-restart on reboot and journalctl integration, install a systemd service:

```bash
sudo tee /etc/systemd/system/corvin-compose.service <<'EOF'
[Unit]
Description=Corvin Docker Compose Stack
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/corvin
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

sudo systemctl daemon-reload
sudo systemctl enable --now corvin-compose
```

Then:
```bash
sudo systemctl status corvin-compose
sudo systemctl restart corvin-compose
sudo journalctl -u corvin-compose -f
```

---

## Troubleshooting

### Container Won't Start
```bash
# Check logs
docker logs corvin

# Verify .env is valid
cat /opt/corvin/.env

# Check permissions on home/
ls -ld /opt/corvin/home
# Should be: drwxr-xr-x 1000:1000

# Rebuild and run interactively
docker compose down
docker compose run --rm corvin /bin/bash
```

### Bridges Not Connecting
1. Verify bridge is enabled in `.env` and in `docker-compose.yml`
2. Check `bridge-settings/*.json` exists and is valid JSON
3. Check API token/credentials are correct
4. View daemon logs: `docker logs corvin | grep bridge-name`

### Can't Access Console
```bash
# Is corvin container running?
docker ps | grep corvin

# Is gateway listening?
docker exec corvin curl http://localhost:8000/healthz

# Is Caddy routing correctly?
docker logs corvin-caddy

# Direct port-forward test
docker exec corvin nc -zv localhost 8000
```

### TLS / Caddy Issues
- Self-signed certs warn in browsers (expected for localhost)
- For Let's Encrypt, ensure `CORVIN_DOMAIN` resolves to this host before start
- Check `/opt/corvin/Caddyfile` syntax: `docker exec corvin-caddy caddy validate`

### Out of Disk Space
Audit chains grow ~1 MB/day. Check:
```bash
du -sh /opt/corvin/home
du -sh /opt/corvin/home/.corvin/tenants/_default/global

# Rotate old audit segments
docker exec corvin \
  /opt/corvin-repo/operator/bridges/shared/audit_sealer.py \
  --action rotate
```

---

## Differences: Docker vs Systemd

| Aspect | Docker | Systemd |
|---|---|---|
| **Installation** | Single `docker-compose` + `.env` | Interactive `setup.sh`, multi-step |
| **Persistence** | Single bind-mount (`/opt/corvin/home`) | Spread across `~/.corvin`, `~/.config`, `~/.claude` |
| **Portability** | Entire instance in `/opt/corvin`, easily backupable | OS-specific paths, tied to user |
| **Isolation** | Container runs as uid 1000, cannot affect host | Direct filesystem/network access |
| **Secrets** | Stored in `/opt/corvin/.env` (mode 0600) | Stored in `~/.config/` (mode 0600) |
| **Logging** | Docker Compose logs, JSON driver, `/var/log/corvin` inside container | systemd journal, per-unit |
| **Restart** | `docker compose restart` or systemd service | `systemctl restart corvin-voice-*` per unit |
| **Development** | Bind-mount repo for live reload (optional) | Run `bridge.sh fg` in terminal |
| **TLS** | Caddy reverse proxy (included) | Manual Nginx/Let's Encrypt setup |
| **Multi-tenant** | Single compose per tenant, mount different `CORVIN_TENANT_ID` | Single systemd per tenant (not yet implemented) |

---

## What You Gain

✅ **Zero host configuration** — Docker handles everything  
✅ **Portable state** — Backup `/opt/corvin/home`, restore anywhere  
✅ **Isolation** — Container doesn't write outside `/opt/corvin`  
✅ **TLS out-of-the-box** — Caddy with Let's Encrypt included  
✅ **Easy updates** — `git checkout` + `docker compose down && up`  
✅ **Single entrypoint** — No juggling 6 systemd units  
✅ **Works on macOS** — Same config, same behavior (no systemd needed)  

---

## Next Steps

1. **Choose deployment:** Bootstrap script or manual setup
2. **Run setup:** Follow Step 1–2 above
3. **Start:** `docker compose up -d`
4. **Access console:** Open http://localhost:8000/console/ (no token needed — loopback auto-login)
5. **Configure bridges:** Edit `bridge-settings/*.json`
6. **Monitor:** `docker logs -f corvin`

For production, add:
- Offsite backup of `/opt/corvin/backups/`
- Monitoring of `localhost:9090/metrics` (Prometheus)
- Alert on `docker healthcheck` failures
- Audit log verification: `docker exec corvin voice-audit.py verify --email ops@...`

---

## References

- **ops/README.md** — Deployment strategies (Caddy, Tailscale, etc.)
- **ops/Dockerfile** — What's installed in the image
- **ops/supervisord.conf** — Process management inside container
- **ops/docker-compose.yml** — Service definitions
- **CONTRIBUTING.md** — Development setup (if you want to hack on Corvin itself)
