# Corvin Docker — Complete Headless Server Guide

## What's Here

This package contains everything you need to run Corvin completely in Docker on a headless server (no GUI required).

### Documents

| File | Purpose |
|------|---------|
| **DOCKER_HEADLESS_SETUP.md** | Comprehensive setup guide (read this first) |
| **docker-quick-start.sh** | Automated setup script (alternative to manual steps) |
| **.env.example** | Annotated configuration template |
| **TROUBLESHOOTING.md** | Common issues + fixes |
| **README.md** | This file |

---

## Quick Decision Tree

### 👤 Solo user, just want to try it?

1. Run the bootstrap installer:
   ```bash
   sudo curl -fsSL https://raw.githubusercontent.com/veegee82/Corvin/main/ops/bootstrap/install.sh | bash
   ```

2. Edit `/opt/corvin/.env` with your `ANTHROPIC_API_KEY`

3. Restart:
   ```bash
   sudo systemctl restart corvin-compose
   ```

4. Access console: `http://localhost:8765/console/` or `http://your.host:8765/console/`

**That's it.** Takes ~5 minutes.

---

### 👥 Team deployment with custom domain?

1. Read: **DOCKER_HEADLESS_SETUP.md** (covers Caddy + Let's Encrypt + TLS)

2. Run the setup script:
   ```bash
   chmod +x docker-quick-start.sh
   sudo ./docker-quick-start.sh
   ```

3. Configure bridges (Discord, Telegram, etc.) in `/opt/corvin/bridge-settings/`

4. Set `CORVIN_DOMAIN` in `.env` (requires DNS A-record)

5. Enable on boot:
   ```bash
   sudo systemctl enable corvin-compose
   ```

---

### 🔒 Private access only (Tailscale)?

Use **Tailscale sidecar** (see ops/tailscale/README.md in the repo):

- No firewall rules needed
- Works behind NAT
- Encrypted end-to-end
- Access from any device on your Tailnet: `https://corvin-hostname/console/`

---

## Architecture At A Glance

```
You (browser)
      ↓
   HTTPS (Caddy)
      ↓
Docker Container
  ├─ Gateway (FastAPI) 
  │  └─ Web Console UI (Next.js, built-in)
  ├─ Adapter (orchestrator)
  │  └─ Forge/SkillForge MCP servers
  ├─ Bridge daemons (Discord, Telegram, etc.)
  └─ Supervisord (process manager)
      ↓
Persistent State (bind-mount)
  /opt/corvin/home/
  ├─ .corvin/tenants/_default/
  │  ├─ global/ (audit chain)
  │  ├─ sessions/ (chat state)
  │  ├─ forge/ (tools)
  │  ├─ skill-forge/ (skills)
  │  └─ voice/ (TTS profile)
```

---

## 5-Minute Setup (Manual)

```bash
# 1. Create directories
sudo mkdir -p /opt/corvin/{home,bridge-settings,tls,backups}

# 2. Clone repo
cd /opt/corvin
sudo git clone --depth=1 https://github.com/veegee82/Corvin.git repo
cd repo && sudo git fetch --tags && \
  sudo git checkout $(git tag -l 'v*' --sort=-v:refname | head -n1)

# 3. Create .env
sudo cp ops/.env.template /opt/corvin/.env
sudo chmod 0600 /opt/corvin/.env
# EDIT: sudo nano /opt/corvin/.env
#   - Set ANTHROPIC_API_KEY
#   - Set CORVIN_DOMAIN (optional, for Let's Encrypt)

# 4. Create docker-compose.yml
sudo cp ops/docker-compose.yml /opt/corvin/docker-compose.yml

# 5. Create Caddyfile
sudo cp ops/Caddyfile.template /opt/corvin/Caddyfile

# 6. Self-signed cert (for localhost/development)
cd /opt/corvin/tls && sudo openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout cert.key -out cert.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"

# 7. Bridge settings (optional)
echo '{"enabled": false}' | sudo tee /opt/corvin/bridge-settings/{discord,telegram,whatsapp,slack,email}.json > /dev/null

# 8. Fix permissions
sudo chown -R 1000:1000 /opt/corvin/home
sudo chmod 0600 /opt/corvin/.env

# 9. Start
sudo docker compose -f /opt/corvin/docker-compose.yml --env-file /opt/corvin/.env up -d

# 10. Monitor
sudo docker logs -f corvin
```

Access: `http://localhost:8765/console/` (wait 30–60 sec for boot)

---

## Access Methods

| Method | URL | Best For |
|--------|-----|----------|
| **Local machine** | `http://localhost:8765/console/` | Development |
| **LAN (self-signed)** | `https://192.168.1.X:8765/console/` (⚠ cert warning) | Small team |
| **Tailscale** | `https://corvin-hostname/console/` (encrypted) | Private, no firewall |
| **Public domain** | `https://corvin.example.com/console/` (Let's Encrypt) | Production |

---

## Key Features Included

✅ **Web Console UI** — Configure bridges, view audit logs, manage personas  
✅ **REST API Gateway** — Programmatic access for CI/CD  
✅ **All Bridge Daemons** — Discord, Telegram, WhatsApp, Slack, Email  
✅ **Forge** — Runtime tool generation (sandboxed, bwrap)  
✅ **SkillForge** — Runtime skill generation (markdown, linted)  
✅ **Audit Chain** — SHA-256-chained, hash-verifiable  
✅ **Multi-tenant** — Independent workspaces per tenant  
✅ **Hot-reload** — Bridge settings change without restart  
✅ **Compliance** — EU AI Act 2026 + GDPR (built-in)  

---

## Common Next Steps

### Enable Discord

1. Create Discord bot at https://discord.com/developers/applications
2. Get the token (copy under "Token")
3. Add the bot to your server (OAuth2 > URL Generator)
4. Create `/opt/corvin/bridge-settings/discord.json`:
   ```json
   {
     "enabled": true,
     "discord_token": "YOUR_TOKEN_HERE",
     "whitelist": [123456789],
     "chat_profiles": {}
   }
   ```
5. Set `CORVIN_BRIDGE_DISCORD=true` in `.env`
6. Restart: `sudo systemctl restart corvin-compose`

### Enable Voice (TTS)

1. Set `OPENAI_API_KEY` in `.env` (for Whisper STT + TTS)
2. Restart
3. Configure listener profile in console: `/voice-user-set jargon=2 style=concise level=intermediate`

### Backup & Restore

```bash
# Backup
tar -czf /opt/corvin/backups/home-$(date +%F).tgz -C /opt/corvin home

# Restore
cd /opt/corvin && tar -xzf backups/home-2026-05-19.tgz
```

### Update to Latest

```bash
cd /opt/corvin/repo
sudo git fetch --tags
sudo git checkout $(git tag -l 'v*' --sort=-v:refname | head -n1)
sudo systemctl restart corvin-compose
```

---

## Troubleshooting

**Console shows 404?**  
→ See TROUBLESHOOTING.md § "Web Console Returns 404"

**Bridge not starting?**  
→ Check `/opt/corvin/bridge-settings/*.json` syntax + token  
→ `sudo docker exec corvin supervisorctl status`

**Out of disk space?**  
→ `du -sh /opt/corvin/home`  
→ Purge old sessions: `find ... -mtime +30 -type d -exec rm -rf {} \;`

**High memory usage?**  
→ Increase `CORVIN_MEM_LIMIT` in `.env`

**Certificate issues?**  
→ For self-signed: browser warning is normal (add exception)  
→ For Let's Encrypt: ensure DNS A-record resolves first

See **TROUBLESHOOTING.md** for complete guide (30+ scenarios covered).

---

## File Structure (Host)

```
/opt/corvin/
├── .env                              ← secrets (mode 0600)
├── docker-compose.yml                ← services + volumes
├── Caddyfile                         ← TLS reverse proxy config
├── repo/                             ← cloned source code
├── home/                             ← persistent state (bind-mount)
│   └── .corvin/tenants/_default/
│       ├── global/                   ← audit chain, vault
│       ├── sessions/                 ← chat-scoped state
│       ├── forge/                    ← runtime tools
│       ├── skill-forge/              ← runtime skills
│       └── voice/                    ← TTS profiles
├── bridge-settings/                  ← read-only config
│   ├── discord.json
│   ├── telegram.json
│   └── ...
├── tls/                              ← certificates
│   ├── cert.pem
│   └── cert.key
└── backups/                          ← timestamped tarballs
```

---

## Support

- **Docs:** https://github.com/veegee82/Corvin/tree/main/docs
- **Issues:** https://github.com/veegee82/Corvin/issues
- **Discussions:** https://github.com/veegee82/Corvin/discussions

---

## Quick Reference

| Task | Command |
|------|---------|
| Check status | `sudo docker compose -f /opt/corvin/docker-compose.yml ps` |
| View logs | `sudo docker logs -f corvin` |
| Restart | `sudo systemctl restart corvin-compose` |
| Shell | `sudo docker exec -it corvin bash` |
| Check health | `sudo docker exec corvin /usr/local/bin/corvin-healthcheck` |
| Verify audit | `sudo docker exec corvin /opt/corvin-repo/operator/voice/scripts/voice-audit.py verify` |
| Update | `cd /opt/corvin/repo && sudo git fetch --tags && sudo git checkout $(git tag -l 'v*' --sort=-v:refname \| head -n1) && sudo systemctl restart corvin-compose` |

---

## License

Apache 2.0 — see LICENSE in the repo.

Compliance: EU AI Act 2026, GDPR Art. 6/7/30/32, hash-chained audit, consent-gate.

---

**Ready to start?** → Read **DOCKER_HEADLESS_SETUP.md** next.
