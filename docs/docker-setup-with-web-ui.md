# Corvin Docker Setup Using the Web UI

## Overview

The **Web Console UI fully covers the setup process** that `setup.sh` would do interactively. This means:

1. **No need for interactive terminal wizard** on a fresh system
2. **Start Docker** → **Access web UI** → **Configure everything there**
3. The UI handles: API keys, bridge tokens, credentials, etc.

---

## Complete Setup Flow (Fresh System → Running Corvin)

### Step 1: Bootstrap Docker (Automated)
```bash
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/veegee82/Corvin/main/ops/bootstrap/install.sh | sudo bash'
```

What this does (non-interactive):
- Install Docker CE, docker-compose, UFW, fail2ban, swap
- Clone repo to `/opt/corvin-repo`, pin to latest version
- Create `/opt/corvin/` directory structure
- Generate `.env.template` (with empty API keys)
- Install `corvin-compose.service` (systemd)
- **Start the container**

**Output after 2-3 minutes:**
```
✓ Corvin provisioned at /opt/corvin
Next steps:
  1. Edit /opt/corvin/.env (OPTIONAL — can skip, do it in web UI)
  2. Restart: systemctl restart corvin-compose
  3. Check: docker compose -f ... ps
```

### Step 2: Access Web Console (No Terminal Needed After This)

The container auto-generates a **one-time setup token** on first start:

```bash
docker logs corvin | grep -A5 "First-Run Setup"
```

Output:
```
╔══════════════════════════════════════════════════════════╗
║  Corvin — First-Run Setup                            ║
║                                                         ║
║  Console access token (shown ONCE — save it now):      ║
║                                                         ║
║  token_abc123def456ghi789jkl012mno345pqr678stu         ║
║                                                         ║
║  Open http://localhost:8000/console and paste it there. ║
╚══════════════════════════════════════════════════════════╝
```

Open in browser:
- **Local:** `http://localhost:8000/console`
- **SSH tunnel:** `ssh -L 8000:localhost:8000 user@host`, then `http://localhost:8000/console`

Paste the token and press Enter. **You now have admin access.**

---

## Web UI Setup Features

### 1. **Dashboard** (`/console/dashboard`)
- System health overview
- Active bridges status
- Session count
- Recent audit events

### 2. **Setup Wizard** (`/console/setup`)

#### A. Engine Keys Management
The UI lets you add API keys for multiple engines:

**Supported Engines:**
- ✅ **Anthropic API** (ANTHROPIC_API_KEY) — [Get key](https://console.anthropic.com/account/keys)
- ✅ **OpenAI / Codex** (OPENAI_API_KEY) — [Get key](https://platform.openai.com/api-keys)
- ✅ **Google Gemini** (GEMINI_API_KEY) — [Get key](https://aistudio.google.com/app/apikey)
- ✅ **OpenRouter** (OPENROUTER_API_KEY) — [Get key](https://openrouter.ai/keys)
- ✅ **Ollama** (OLLAMA_BASE_URL) — Local LLM server
- ✅ **Claude Code** (OAuth) — Auto-detected if logged in

**How to add a key:**
1. Navigate to `/console/setup` (in the web UI)
2. Click "Add Engine Key"
3. Select the engine (e.g., Anthropic API)
4. Paste your API key
5. Re-authenticate (security prompt)
6. Save

The key is written to `/opt/corvin/.env` securely (mode 0600).

#### B. Bridge Configuration
The UI provides **step-by-step setup guides** for each bridge:

**Discord**
1. Go to discord.com/developers/applications
2. Create a new application
3. Add a bot user
4. Copy the bot token
5. Paste token in the UI — that's all that's required
6. Bridge auto-connects (DMs + @mentions work immediately; optionally enable
   Message Content Intent for full guild-channel reading)

**Telegram**
1. Chat with @BotFather on Telegram
2. Send `/newbot`
3. Copy the API token
4. Paste in the UI
5. Send `/start` to your bot

**WhatsApp**
- QR code displayed in the UI (auto-refreshes)
- Scan with your phone
- Bridge pairs automatically

**Slack**
1. Go to api.slack.com/apps
2. Create new app
3. Add bot scopes
4. Copy bot token (xoxb-...)
5. Paste in UI

**Email**
- Configure IMAP/SMTP
- Or use Gmail with app password
- Bridge polls every 30s

**Signal**
- Install signal-cli-rest-api (local)
- Register phone number
- Paste phone in UI

**Each bridge guide includes:**
- ✅ Clickable links to provider setup pages
- ✅ Step-by-step numbered instructions (in Markdown)
- ✅ Field to paste credentials
- ✅ Status indicator (configured / not configured)
- ✅ Masked token display (****ABCD for security)

---

## Complete Setup Workflow (No Terminal After Bootstrap)

### Scenario: Fresh Ubuntu 22.04 server, no prior setup

**Prerequisites:** Root/sudo access, internet connectivity

**Duration:** ~5 minutes

#### 1. Run Bootstrap (1 command, ~2 minutes)
```bash
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/veegee82/Corvin/main/ops/bootstrap/install.sh | sudo bash'
```

#### 2. Wait for Container Health
```bash
# Terminal 2 (while bootstrap runs)
sudo docker compose -f /opt/corvin/docker-compose.yml logs -f
```

Bootstrap will:
- Install packages
- Start container
- Exit with success message

#### 3. Open Browser

#### 4. Open Browser & Authenticate
Open `http://localhost:8000/console` (or your domain if DNS is set)
- Paste token
- Click "Authenticate"

**Now you're in the dashboard.** Everything else is point-and-click.

#### 5. Add API Key(s)
In the console:
- Navigate to **Settings → Engines** (or `/console/setup`)
- Click **Add Anthropic API Key**
- Paste your key from console.anthropic.com
- Save

#### 6. Configure Bridges
For each bridge you want:
- Navigate to **Settings → Bridges** (or `/console/setup`)
- Select the bridge (Discord, Telegram, etc.)
- Follow the 5-step guide
- Paste credentials
- Save

The UI shows:
- ✅ "Not Configured" → "Configured" status
- 🔄 Live daemon status (running/stopped)
- 📊 Message counts, latency

#### 7. Done
The bridges auto-connect. You can now message the bot from Discord/Telegram/etc. and it will respond via Claude.

---

## Comparison: Bootstrap + Web UI vs. `setup.sh`

| Feature | `setup.sh` (Systemd) | Bootstrap + Web UI (Docker) |
|---|---|---|
| **OS Detection** | Interactive prompt | Automatic (detect at bootstrap time) |
| **Package Install** | Interactive choices | All included in Docker image |
| **Claude Code Setup** | Prompts for login | Detected if already installed |
| **API Key Entry** | Terminal prompt | Web UI form (with links to providers) |
| **Bridge Setup** | Interactive questions | Web UI guides with step-by-step instructions |
| **Validation** | Built into script | Dashboard shows health + status |
| **Multi-user** | Single user (~/.corvin) | Multi-user via web console (roles, permissions) |
| **Portability** | Host-specific | Entire `/opt/corvin` is portable |
| **TLS** | Manual setup | Auto via Caddy (with Let's Encrypt) |
| **Time to Start** | 15-30 minutes | 5 minutes (bootstrap only) |

---

## Advanced Setup (After Initial Onboarding)

### Bind-Mount the Repo (for Development)
In `/opt/corvin/docker-compose.yml`, uncomment:
```yaml
volumes:
  # ...
  - ~/Corvin:/opt/corvin-repo:ro
```

Then changes to Python/JS code are live (hot-reload on next request).

### Custom Domain + TLS
Edit `.env`:
```bash
CORVIN_DOMAIN=corvin.example.com
CORVIN_ACME_EMAIL=admin@example.com
```

Then restart:
```bash
systemctl restart corvin-compose
```

Caddy auto-provisions Let's Encrypt cert.

### Add More Users (Web UI)
In console:
- Settings → Members
- Click "Invite User"
- Choose role (admin, member, observer)
- Share link

User scans QR or clicks link → auto-authenticated.

### Manage Personas (Web UI)
In console:
- Settings → Personas
- Create custom agent roles
- Configure per-persona tools, MCP servers, LDD settings
- Route requests to personas

### Audit & Compliance (Web UI)
In console:
- Audit → View all events (hash-chained, tamper-evident)
- Export compliance reports
- Verify audit chain integrity
- GDPR erasure requests via CLI

---

## File Structure After Setup

```
/opt/corvin/
├── docker-compose.yml              (from ops/, don't edit)
├── Caddyfile                       (from ops/template, edit for TLS)
├── .env                            (EDIT: your API keys, domain)
├── home/
│   └── .corvin/
│       ├── tenants/_default/
│       │   ├── global/
│       │   │   ├── auth/
│       │   │   │   └── gateway_tokens.json (user tokens for API access)
│       │   │   ├── vault/
│       │   │   │   └── secrets.json (encrypted API keys if using vault)
│       │   │   └── audit/
│       │   │       └── audit.jsonl (tamper-evident hash chain)
│       │   ├── sessions/           (per-chat session state)
│       │   ├── forge/              (generated tools)
│       │   ├── skill-forge/        (generated skills)
│       │   └── voice/              (bridge daemon state)
│       ├── .claude/
│       │   └── credentials.json    (Claude Code login state, if used)
│       └── logs/
│           └── corvin.log         (all services)
├── bridge-settings/                (read-only into container)
│   ├── discord.json                (if CORVIN_BRIDGE_DISCORD=true)
│   ├── telegram.json
│   └── ...
├── tls/                            (self-signed or Let's Encrypt)
│   ├── cert.pem
│   └── cert.key
└── backups/                        (operator-managed tarballs)
```

---

## Troubleshooting

### Can't See the Setup Button
The setup routes are only visible to **authenticated users with admin role**.
- Open `http://localhost:8000/console/` — no token needed (loopback auto-login).
- If accessing from a non-localhost IP, OIDC authentication is required.

### API Key Not Saving
- Check browser console for errors (F12 → Console)
- Check docker logs: `docker logs corvin | tail -50`
- Permissions: `/opt/corvin/.env` must be writable by uid 1000

### Bridge Not Connecting After Setup
1. Check dashboard: does it show "Running"?
2. Check bridge logs: `docker logs corvin | grep bridge-name`
3. Is the API token correct? (UI shows ****ABCD for masked validation)
4. For Discord/Telegram, did you invite the bot to the server/group?

### Lost the First-Run Token
Generate a new one:
```bash
docker exec corvin python3 -m corvin_gateway.cli token issue _default --label "recovery"
```

---

## Why Web UI for Setup?

✅ **Cross-platform:** Works on Linux, macOS, Windows (WSL2), even mobile browsers  
✅ **No terminal knowledge required:** Point-and-click  
✅ **Built-in guidance:** Each bridge has a linked how-to  
✅ **Validation at save time:** No "invalid config" surprises at restart  
✅ **Multi-user:** Different team members can each authenticate  
✅ **Audit trail:** Every API key update is logged  
✅ **No `setup.sh` interpreter needed:** Bash/Python knowledge not required  
✅ **Progressive:** Configure one bridge, test, add another  

---

## Next Steps

1. **Run bootstrap:** `sudo bash -c 'curl ... | bash'`
2. **Open browser:** `http://localhost:8000/console/` (no token needed — loopback auto-login)
3. **Add API key:** Settings → Engines → Anthropic
6. **Add bridges:** Settings → Bridges
7. **Test:** Send a message to your bot
8. **Monitor:** Audit → view all events

You now have a fully configured, production-ready Corvin instance, all from the web UI.
